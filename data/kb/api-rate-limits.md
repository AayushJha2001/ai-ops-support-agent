# API Rate Limits

## Default limits
The Acme Cloud API allows 60 requests per minute on Starter, 300 on Team, 1,000 on Business and 5,000 on Enterprise. Limits are applied per API key.

## Rate limit responses
When a limit is exceeded the API returns HTTP 429 with a Retry-After header in seconds. Clients should back off exponentially and retry after the indicated delay.

## Raising limits
Enterprise customers can request a temporary or permanent limit increase by opening a ticket in the "technical" category describing the expected traffic. Other plans must upgrade to get higher limits.

## Troubleshooting 401 and 403 errors
A 401 response means the API key is missing or invalid; regenerate the key under Settings > API keys. A 403 response means the key does not have the required scope.
