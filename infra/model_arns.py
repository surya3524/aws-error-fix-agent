"""Derive least-privilege Bedrock resource ARNs from the configured model / inference profile ID.

No model IDs live in this repo: the ID is supplied at deploy time (CDK context `modelId`).
"""

from __future__ import annotations

# Cross-region inference profile prefixes (see Claude Code on Amazon Bedrock docs).
GEO_PREFIXES = ("us", "eu", "apac", "jp", "au", "global", "us-gov")


def bedrock_invoke_resources(model_id: str, partition: str, region: str, account: str) -> list[str]:
    """Resources for bedrock:InvokeModel / InvokeModelWithResponseStream.

    * Application inference profile ARN -> the ARN itself + Anthropic foundation models it may route to.
    * System cross-region profile ID (e.g. "<geo>.<provider>.<model>") -> the profile in this region
      + the underlying foundation model in any region the profile can route to.
    * Plain foundation-model ID -> that model in this region.
    """
    model_id = model_id.strip()
    if not model_id:
        raise ValueError("modelId is required")
    if model_id.startswith("arn:"):
        # VERIFY: an application profile's backing model is not knowable at synth time;
        # scope to the provider's models. Tighten to the exact model if you know it.
        return [model_id, f"arn:{partition}:bedrock:*::foundation-model/anthropic.*"]
    head, _, rest = model_id.partition(".")
    if head in GEO_PREFIXES and rest:
        return [
            f"arn:{partition}:bedrock:{region}:{account}:inference-profile/{model_id}",
            # VERIFY: AWS requires the foundation model in every destination region of a
            # cross-region profile, hence the region wildcard (resource only, not action).
            f"arn:{partition}:bedrock:*::foundation-model/{rest}",
        ]
    return [f"arn:{partition}:bedrock:{region}::foundation-model/{model_id}"]
