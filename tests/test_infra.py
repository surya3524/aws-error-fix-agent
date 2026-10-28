import pytest

from model_arns import bedrock_invoke_resources


def test_cross_region_profile():
    res = bedrock_invoke_resources("us.provider.model-x", "aws", "us-east-1", "123")
    assert res == [
        "arn:aws:bedrock:us-east-1:123:inference-profile/us.provider.model-x",
        "arn:aws:bedrock:*::foundation-model/provider.model-x",
    ]


def test_plain_foundation_model():
    assert bedrock_invoke_resources("provider.model-x", "aws", "eu-west-1", "123") == [
        "arn:aws:bedrock:eu-west-1::foundation-model/provider.model-x"]


def test_application_profile_arn():
    arn = "arn:aws:bedrock:us-east-1:123:application-inference-profile/abc"
    assert bedrock_invoke_resources(arn, "aws", "us-east-1", "123")[0] == arn


def test_empty_rejected():
    with pytest.raises(ValueError):
        bedrock_invoke_resources(" ", "aws", "us-east-1", "123")
