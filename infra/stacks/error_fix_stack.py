"""One stack: AgentCore runtime (sandbox) + trusted orchestrator + storage + notifications."""

from __future__ import annotations

import os

from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_bedrockagentcore as agentcore,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_logs_destinations as log_destinations,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
)
from constructs import Construct

from model_arns import bedrock_invoke_resources

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _required(scope: Construct, key: str) -> str:
    value = scope.node.try_get_context(key)
    if not value:
        raise ValueError(f"Missing required CDK context '{key}'. Pass -c {key}=... (see docs/SETUP.md)")
    return str(value)


class ErrorFixStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        model_id = _required(self, "modelId")
        log_group_name = _required(self, "logGroupName")
        azdo_org = _required(self, "azdoOrg")
        azdo_project = _required(self, "azdoProject")
        azdo_repo = _required(self, "azdoRepo")
        notify_email = self.node.try_get_context("notifyEmail")
        filter_pattern = self.node.try_get_context("filterPattern") or "?ERROR ?Exception ?Traceback"
        fallback_branch = self.node.try_get_context("fallbackBranch") or "main"
        max_concurrency = int(self.node.try_get_context("maxConcurrentAnalyses") or 2)

        # ---------------- storage + notifications
        bucket = s3.Bucket(
            self, "Artifacts",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            # No auto_delete_objects: its helper Lambda needs wildcard S3 actions.
            # Empty the bucket before `cdk destroy` (see docs/SETUP.md, teardown).
            removal_policy=RemovalPolicy.DESTROY,
            lifecycle_rules=[
                s3.LifecycleRule(prefix="snapshots/", expiration=Duration.days(7)),
                s3.LifecycleRule(prefix="results/", expiration=Duration.days(90)),
            ],
        )
        table = dynamodb.Table(
            self, "Incidents",
            partition_key=dynamodb.Attribute(name="fingerprint", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.DESTROY,
        )
        topic = sns.Topic(self, "Suggestions", display_name="AI error-fix suggestions")
        if notify_email:
            topic.add_subscription(subs.EmailSubscription(notify_email))

        # Value is set by you after deploy (aws secretsmanager put-secret-value); never in code.
        pat_secret = secretsmanager.Secret(
            self, "AzureDevOpsPat",
            description="Azure DevOps PAT (Code: Read) used only by the orchestrator Lambda",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ---------------- optional hardening: sandbox in a VPC with NO internet route
        network = None
        if str(self.node.try_get_context("sandboxVpc") or "").lower() == "true":
            network = self._isolated_sandbox_network()

        # ---------------- the sandbox: AgentCore Runtime (one microVM per session)
        runtime = agentcore.Runtime(
            self, "AgentRuntime",
            runtime_name="error_fix_agent",
            description="Read-only error root-cause agent (Claude Agent SDK on Bedrock)",
            agent_runtime_artifact=agentcore.AgentRuntimeArtifact.from_asset(
                os.path.join(ROOT, "agent"),
                platform=ecr_assets.Platform.LINUX_ARM64,  # AgentCore needs ARM64 images
            ),
            environment_variables={
                "CLAUDE_CODE_USE_BEDROCK": "1",
                "AWS_REGION": self.region,
                "ANTHROPIC_MODEL": model_id,
                "AGENT_MAX_TURNS": str(self.node.try_get_context("agentMaxTurns") or 30),
                "AGENT_MAX_BUDGET_USD": str(self.node.try_get_context("agentMaxBudgetUsd") or 2),
            },
            network_configuration=network,  # None -> public network mode (MVP default)
            lifecycle_configuration=agentcore.LifecycleConfiguration(
                idle_runtime_session_timeout=Duration.minutes(5),
                max_lifetime=Duration.hours(1),
            ),
        )
        # The sandbox role gets model invocation only (plus the logging/ECR basics the
        # L2 construct adds). No S3, Secrets Manager, DynamoDB or Azure DevOps access.
        runtime.add_to_role_policy(iam.PolicyStatement(
            sid="InvokeConfiguredModelOnly",
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=bedrock_invoke_resources(model_id, self.partition, self.region, self.account),
        ))

        # ---------------- trusted orchestrator
        orchestrator_dir = os.path.join(ROOT, "orchestrator")
        fn = lambda_.Function(
            self, "Orchestrator",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(
                orchestrator_dir,
                bundling=BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_13.bundling_image,
                    # boto3 is pure Python, so no arm64 emulation is needed for bundling.
                    command=["bash", "-c",
                             "pip install -r requirements.txt -t /asset-output && cp -au *.py /asset-output/"],
                ),
            ),
            memory_size=1024,
            timeout=Duration.minutes(15),
            reserved_concurrent_executions=max_concurrency if max_concurrency > 0 else None,
            retry_attempts=0,
            max_event_age=Duration.hours(1),
            environment={
                "TABLE_NAME": table.table_name,
                "BUCKET_NAME": bucket.bucket_name,
                "TOPIC_ARN": topic.topic_arn,
                "AGENT_RUNTIME_ARN": runtime.agent_runtime_arn,
                "AZDO_PAT_SECRET_ID": pat_secret.secret_arn,
                "AZDO_ORG": azdo_org,
                "AZDO_PROJECT": azdo_project,
                "AZDO_REPO": azdo_repo,
                "AZDO_FALLBACK_BRANCH": fallback_branch,
                "COMMIT_SHA_FIELD": self.node.try_get_context("commitShaField") or "commit_sha",
            },
            log_group=logs.LogGroup(
                self, "OrchestratorLogs",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )
        # Explicit, non-wildcard actions (CDK's grant_* helpers emit things like s3:List*).
        fn.add_to_role_policy(iam.PolicyStatement(
            sid="DedupeTable",
            actions=["dynamodb:PutItem", "dynamodb:UpdateItem"],
            resources=[table.table_arn],
        ))
        fn.add_to_role_policy(iam.PolicyStatement(
            sid="Snapshots",
            actions=["s3:GetObject", "s3:PutObject"],
            resources=[bucket.arn_for_objects("snapshots/*")],
        ))
        fn.add_to_role_policy(iam.PolicyStatement(
            sid="SnapshotCacheLookup",  # ListObjectsV2 under snapshots/ only
            actions=["s3:ListBucket"],
            resources=[bucket.bucket_arn],
            conditions={"StringLike": {"s3:prefix": ["snapshots/*"]}},
        ))
        fn.add_to_role_policy(iam.PolicyStatement(
            sid="Results",
            actions=["s3:PutObject"],
            resources=[bucket.arn_for_objects("results/*")],
        ))
        topic.grant_publish(fn)
        pat_secret.grant_read(fn)
        runtime.grant_invoke_runtime(fn)
        fn.add_to_role_policy(iam.PolicyStatement(
            sid="StopAgentSessions",
            actions=["bedrock-agentcore:StopRuntimeSession"],
            resources=[runtime.agent_runtime_arn, f"{runtime.agent_runtime_arn}/*"],
        ))

        # ---------------- wire the app's error logs to the orchestrator
        logs.SubscriptionFilter(
            self, "ErrorSubscription",
            log_group=logs.LogGroup.from_log_group_name(self, "AppLogGroup", log_group_name),
            destination=log_destinations.LambdaDestination(fn),
            filter_pattern=logs.FilterPattern.literal(filter_pattern),
        )

        CfnOutput(self, "AgentRuntimeArn", value=runtime.agent_runtime_arn)
        CfnOutput(self, "OrchestratorFunctionName", value=fn.function_name)
        CfnOutput(self, "ArtifactsBucketName", value=bucket.bucket_name)
        CfnOutput(self, "IncidentsTableName", value=table.table_name)
        CfnOutput(self, "SuggestionsTopicArn", value=topic.topic_arn)
        CfnOutput(self, "AzureDevOpsPatSecretArn", value=pat_secret.secret_arn)

    def _isolated_sandbox_network(self) -> agentcore.RuntimeNetworkConfiguration:
        """Private isolated subnets, no NAT/IGW route; only Bedrock Runtime + S3 via VPC endpoints.

        VERIFY before relying on it: confirm in the AgentCore docs which other endpoints a
        VPC-mode runtime needs in your region (e.g. CloudWatch Logs), and test that the agent
        cannot reach the internet (see docs/SETUP.md, Hardening).
        """
        vpc = ec2.Vpc(
            self, "SandboxVpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[ec2.SubnetConfiguration(
                name="sandbox", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24)],
        )
        vpc.add_gateway_endpoint("S3", service=ec2.GatewayVpcEndpointAwsService.S3)
        vpc.add_interface_endpoint("BedrockRuntime", service=ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME)
        vpc.add_interface_endpoint("Logs", service=ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS)
        sg = ec2.SecurityGroup(self, "SandboxSg", vpc=vpc, allow_all_outbound=False,
                               description="AgentCore sandbox: HTTPS to VPC endpoints only")
        # Isolated subnets have no route to the internet; the only reachable HTTPS targets
        # are the VPC endpoints (Bedrock Runtime, Logs) and S3 via the gateway endpoint.
        sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS (endpoints only; no IGW/NAT)")
        return agentcore.RuntimeNetworkConfiguration.using_vpc(
            self, vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[sg],
        )
