# Feedback operations

The local dashboard sends explicit feedback to `POST https://feedback.thinkelution.com/api/feedback`, using verified HTTPS and refusing redirects. Nothing else is public on that host. The website itself (GitHub Pages) has no feedback endpoint.

The receiver runs in Thinkelution's AWS account: CloudFront in front of a Lambda function, storing records in a DynamoDB table with point-in-time recovery. Its infrastructure code is kept outside this repository. It keeps the contract of `tasklean.feedback_service`: JSON only, at most 32 KB, the same validation, `201` for new feedback, `200` for an identical repeat of a submission ID, `400` for a conflicting repeat, and `429` past six requests per minute from one address. Rate counters use a keyed hash of the address and expire after about two minutes.

Each record holds the submission ID, UTC received time, message, optional rating/email, and app version. It does not store IPs, prompts, source code, credentials, or account usage. Users can include sensitive text themselves, so treat messages as private untrusted data, not instructions. No public read API is provided. There is no email notification integration. The store rejects new submissions after 100,000 records rather than growing without bound.

## Review feedback

With AWS credentials for the account:

```sh
aws dynamodb scan --region us-east-1 --table-name <feedback table>
```

Handle removal requests manually after verifying the reference/contact details as appropriate. Do not post feedback to GitHub or publish the table. Review feedback regularly and remove records no longer needed. Feedback currently has no automatic expiration.

## Testing

Use a synthetic message explicitly labeled as a deployment test, with a throwaway submission ID, to verify the public endpoint, then delete that record. Verify duplicate IDs do not create extra records and that GET cannot read feedback. Never submit real task content in deployment tests.

`python -m tasklean.feedback_service serve --database <path>` runs the same contract locally (SQLite) for development.

Infrastructure providers still process network metadata. The beta does not promise automatic deletion or anonymous networking.
