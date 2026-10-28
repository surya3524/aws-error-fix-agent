# Reading code from a private S3 bucket (COBOL pilot)

The agent can read code from S3 instead of the bundled `sample_app/`. Two environment variables on
the runtime control this:

| Variable | Example | Meaning |
|---|---|---|
| `CODE_BUCKET` | `acme-rca-code-mirror-043309323494` | Bucket that holds the code mirror. If it isn't set, the agent uses `sample_app/`. |
| `CODE_PREFIX` | `cobol-sample` | Folder inside the bucket. The agent can't read anything outside it. |

The agent has three read-only tools: `list_files`, `search_code` (case-insensitive text search)
and `read_file`.
- Paths that try to leave the prefix, such as `../`, come back as "file not found".
- Files are truncated at 200 KB.
- S3 errors are returned to the model as tool errors, not as crashes.

The code itself never goes into `agent.zip`.

`sample_cobol/` has a pilot with a planted bug: `PAYCALC.cbl`, the copybook `EMPREC.cpy` and the
JCL `PAYJOB.jcl`. `PAYCALC` adds `EMP-HOURS` (`PIC 9(3)`) without checking it. A timesheet record
with a blank hours field causes an **S0C7** abend at `PAYCALC.cbl` line 33.
`console_test_cobol.json` is the matching job log.

## Setup (console, region us-east-2)

### 1. Create the bucket

1. Go to **S3 → Create bucket**.
2. **Name:** for example `acme-rca-code-mirror-<account-id>`.
3. **Block all public access:** leave it **on**.
4. **Bucket Versioning:** choose **Enable**, so every code sync is kept and auditable.
5. **Default encryption:** choose **SSE-KMS**.
   - With the AWS managed key `aws/s3`, there's nothing more to do.
   - With your own key, also add the `kms:Decrypt` statement in step 3.
6. Choose **Create bucket**.
7. Open **Permissions → Bucket policy** and paste this policy, which refuses any access that isn't
   over TLS:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "DenyInsecureTransport",
    "Effect": "Deny",
    "Principal": "*",
    "Action": "s3:*",
    "Resource": ["arn:aws:s3:::BUCKET", "arn:aws:s3:::BUCKET/*"],
    "Condition": {"Bool": {"aws:SecureTransport": "false"}}
  }]
}
```

### 2. Upload the pilot code

1. In the bucket, choose **Create folder** and name it `cobol-sample`.
2. Open the folder and upload the three files from `sample_cobol/`.

In production, a pipeline does this step instead of a person (see `docs/architecture.md`).

### 3. Give the execution role read-only access

Go to **IAM → Roles → AmazonBedrockAgentCoreRuntimeDefaultServiceRole-…** and choose **Add
permissions → Create inline policy → JSON**. Paste this policy and name it `ReadCodeMirror`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListOnlyThePrefix",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::BUCKET",
      "Condition": {"StringLike": {"s3:prefix": ["cobol-sample/", "cobol-sample/*"]}}
    },
    {
      "Sid": "ReadOnlyThePrefix",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::BUCKET/cobol-sample/*"
    }
  ]
}
```

If the bucket uses your own KMS key, add this statement:

```json
{
  "Sid": "DecryptViaS3Only",
  "Effect": "Allow",
  "Action": "kms:Decrypt",
  "Resource": "arn:aws:kms:us-east-2:ACCOUNT:key/KEY-ID",
  "Condition": {"StringEquals": {"kms:ViaService": "s3.us-east-2.amazonaws.com"}}
}
```

There are no write, delete or other-bucket permissions. The agent can only read this one folder.

### 4. Deploy the new agent.zip and set the variables

1. Upload the new `agent.zip`. It has the S3 tools; the code bucket is configured separately.
2. Keep **Python 3.13** and **Entry point** `main.py`.
3. On the agent's **Update** page, look for **Environment variables** and add:
   - `CODE_BUCKET` = your bucket name
   - `CODE_PREFIX` = `cobol-sample`
4. If the console has no environment-variable field, run this in **CloudShell** instead. It
   re-applies the current artifact, role and network settings and adds the variables:

```bash
RID=runtime_aws_error-4Vh607EJvc; R=us-east-2; BUCKET=acme-rca-code-mirror-043309323494
aws bedrock-agentcore-control get-agent-runtime --region $R --agent-runtime-id $RID > /tmp/rt.json
aws bedrock-agentcore-control update-agent-runtime --region $R --agent-runtime-id $RID \
  --agent-runtime-artifact "$(jq -c .agentRuntimeArtifact /tmp/rt.json)" \
  --role-arn "$(jq -r .roleArn /tmp/rt.json)" \
  --network-configuration "$(jq -c .networkConfiguration /tmp/rt.json)" \
  --environment-variables CODE_BUCKET=$BUCKET,CODE_PREFIX=cobol-sample
```

Notes on this command:
- `--environment-variables` replaces the whole set. Add `MODEL_ID=...` to it if you use that
  variable.
- Each update creates a new version. Wait for **Ready** before testing.

### 5. Test

1. Click **Test agent** and paste the contents of `console_test_cobol.json`.
2. The answer should name:
   - `PAYCALC.cbl` line 33 (`ADD EMP-HOURS TO WS-TOTAL-HOURS`) and `EMPREC.cpy` line 7
     (`EMP-HOURS PIC 9(3)`);
   - the cause: the hours field is blank (spaces) in record 1187, which isn't valid numeric data;
   - a fix such as validating with `IF EMP-HOURS IS NUMERIC` before the arithmetic, and
     rejecting or defaulting bad records upstream;
   - high or medium confidence.

To switch back to the Python sample, remove `CODE_BUCKET`.

## Troubleshooting

All errors appear in the runtime's CloudWatch log group.

| What you see | Cause | Fix |
|---|---|---|
| `tool list_files failed: ClientError('...AccessDenied...')` | The role policy is missing or the prefix is wrong | Re-check step 3; the prefix in the policy must match `CODE_PREFIX` |
| `KMS.AccessDeniedException` | The bucket uses your own key | Add the `kms:Decrypt` statement |
| `list_files` returns `(no files)` | The files aren't under `CODE_PREFIX/` | Check the folder name in S3 |
| Answer has low confidence | The model didn't search enough | Try a stronger model, or include the program name in the log |
