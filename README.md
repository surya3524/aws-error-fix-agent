# aws-error-fix-agent

An agent that reads an application error (stack trace, log context) together with
the application's codebase (hosted in Azure DevOps) and returns the likely root
cause and a fix suggestion in 2–3 sentences, with the files/lines involved and a
confidence level. It runs on Amazon Bedrock AgentCore with Claude on Amazon Bedrock.

This phase only suggests fixes: it does not create pull requests or change code.

**Status:** scaffolding. Implementation and the AWS setup guide are coming.

- Design: [docs/design.md](docs/design.md)
- Quick console test (deploy the agent and try it in ~30 min): [quick-test/README.md](quick-test/README.md)
