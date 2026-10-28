# Sample buggy app

A deliberately broken order-pricing module used to try the agent locally.
Checking out order `A101` (a cart whose only line has quantity 0) raises an exception;
`../sample-error.json` is the structured log line that error produces.
