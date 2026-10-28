#!/usr/bin/env python3
import os

import aws_cdk as cdk

from stacks.error_fix_stack import ErrorFixStack

app = cdk.App()
ErrorFixStack(
    app,
    "ErrorFixAgentStack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION"),
    ),
)
app.synth()
