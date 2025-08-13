# NExtSEEK API Documentation

**Version:** 1.0.0  
**Last Updated:** July 2025

## Overview

The NExtSEEK API provides RESTful endpoints for accessing sample trees, NHP (Non-Human Primate) data, and sample queries. This API is built using Django REST Framework and follows OpenAPI 3.0 specification.

## Base URL

```
https://your-domain.com/api/
```

## Authentication

The API supports multiple authentication methods:

### Token Authentication (Recommended)
```bash
# Get your token from Django admin or create via API
curl -X POST https://your-domain.com/api/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "your_username", "password": "your_password"}'

# Use token in requests
curl -H "Authorization: Token your_token_here" \
  https://your-domain.com/api/samples/123/tree/
```

### Session Authentication
Use Django's session authentication for web applications.

### Basic Authentication
Username/password authentication (less secure, not recommended for production).

## API Endpoints

### 1. Sample Tree Endpoints

#### Get Sample Tree by ID
**Endpoint:** `GET /api/samples/{id}/tree/`

**Description:** Retrieve sample tree data by numeric sample ID.

**Parameters:**
- `id` (integer, path): Numeric sample ID

**Example Request:**
```bash
curl -H "Authorization: Token your_token" \
  https://your-domain.com/api/samples/123/tree/
```

**Example Response:**
```json
[
  {
    "id": "123",
    "uuid": "abc-def-123-456",
    "type": "Sample",
    "color": "#FF0000",
    "parentIds": ["456", "789"]
  },
  {
    "id": "456", 
    "uuid": "def-456-789-abc",
    "type": "Parent",
    "color": "#00FF00",
    "parentIds": []
  }
]
```

#### Get Sample Tree by UUID
**Endpoint:** `GET /api/samples-uuid/{uuid}/tree/`

**Description:** Retrieve sample tree data by UUID.

**Parameters:**
- `uuid` (string, path): Sample UUID (format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)

**Example Request:**
```bash
curl -H "Authorization: Token your_token" \
  https://your-domain.com/api/samples-uuid/abc-def-123-456/tree/
```

### 2. NHP (Non-Human Primate) Endpoints

#### Get NHP Information
**Endpoint:** `GET /api/nhp/{nhp_name}/info/`

**Description:** Retrieve NHP metadata and information.

**Parameters:**
- `nhp_name` (string, path): NHP identifier (e.g., "FLY001")

**Example Request:**
```bash
curl -H "Authorization: Token your_token" \
  https://your-domain.com/api/nhp/FLY001/info/
```

**Example Response:**
```json
{
  "id": "FLY001",
  "metadata": {
    "species": "Macaca mulatta",
    "age": 5,
    "weight": 12.5
  },
  "last_updated": "2023-12-01T10:30:00Z"
}
```

#### Get NHP Event Data
**Endpoint:** `GET /api/nhp/{nhp_name}/events/{event_type}/{date}/`

**Description:** Retrieve specific event data for an NHP on a given date.

**Parameters:**
- `nhp_name` (string, path): NHP identifier
- `event_type` (string, path): Type of event (e.g., "feeding", "medical")
- `date` (string, path): Date in YYYY-MM-DD format

**Example Request:**
```bash
curl -H "Authorization: Token your_token" \
  https://your-domain.com/api/nhp/FLY001/events/feeding/2023-12-01/
```

#### Get NHP Timeline
**Endpoint:** `GET /api/nhp/{nhp_name}/timeline/`

**Description:** Retrieve complete timeline data for an NHP.

**Example Request:**
```bash
curl -H "Authorization: Token your_token" \
  https://your-domain.com/api/nhp/FLY001/timeline/
```

#### Download NHP Data (Excel)
**Endpoint:** `GET /api/nhp/{nhp_name}/download/`

**Description:** Download NHP data as an Excel file.

**Response:** Binary Excel file with Content-Type: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`

**Example Request:**
```bash
curl -H "Authorization: Token your_token" \
  -o FLY001_data.xlsx \
  https://your-domain.com/api/nhp/FLY001/download/
```

### 3. Sample Query Endpoints

#### Retrieve Samples (Paginated)
**Endpoint:** `GET /api/sample-queries/retrieve-samples/`

**Description:** Retrieve sample data with pagination support.

**Query Parameters:**
- `page` (integer, optional): Page number (default: 1)
- `page_size` (integer, optional): Number of items per page (default: 100, max: 1000)

**Example Request:**
```bash
curl -H "Authorization: Token your_token" \
  "https://your-domain.com/api/sample-queries/retrieve-samples/?page=2&page_size=50"
```

**Example Response:**
```json
{
  "count": 500,
  "next": "https://your-domain.com/api/sample-queries/retrieve-samples/?page=3&page_size=50",
  "previous": "https://your-domain.com/api/sample-queries/retrieve-samples/?page=1&page_size=50",
  "results": [
    {
      "id": 101,
      "title": "Sample 101",
      "created_date": "2023-11-15"
    },
    // ... more samples
  ]
}
```

### 4. Admin Endpoints

#### Admin Sample Retrieval
**Endpoint:** `GET /api/admin/samples/admin-retrieve-samples/`

**Description:** Admin-only endpoint for retrieving samples with enhanced permissions.

**Authentication:** Requires admin privileges (IsAdminUser)

**Example Request:**
```bash
curl -H "Authorization: Token admin_token" \
  https://your-domain.com/api/admin/samples/admin-retrieve-samples/
```

## Error Responses

### Common HTTP Status Codes

- **200 OK**: Request successful
- **401 Unauthorized**: Authentication required or failed
- **403 Forbidden**: Insufficient permissions (e.g., non-admin accessing admin endpoints)
- **404 Not Found**: Resource not found (invalid ID, UUID, or NHP name)
- **500 Internal Server Error**: Server error

### Error Response Format

```json
{
  "detail": "Error message description"
}
```

### Example Error Responses

**401 Unauthorized:**
```json
{
  "detail": "Authentication credentials were not provided."
}
```

**403 Forbidden:**
```json
{
  "detail": "You do not have permission to perform this action."
}
```

**404 Not Found:**
```json
{
  "detail": "Sample with ID 999999 not found."
}
```

## Pagination

Large datasets are paginated using page-based pagination:

### Pagination Response Format
```json
{
  "count": 1500,
  "next": "https://your-domain.com/api/endpoint/?page=3",
  "previous": "https://your-domain.com/api/endpoint/?page=1", 
  "results": [/* array of results */]
}
```

### Pagination Parameters
- `page`: Page number (starts from 1)
- `page_size`: Items per page (default: 100, maximum: 1000)

## Interactive Documentation

### Swagger UI
Access interactive API documentation at:
```
https://your-domain.com/api/swagger/
```

### ReDoc
Alternative documentation interface:
```
https://your-domain.com/api/redoc/
```

### OpenAPI Schema
Download the complete OpenAPI schema:
```
https://your-domain.com/api/schema/
```

## Rate Limiting

Currently, no rate limiting is implemented. Consider implementing throttling for production use.

## CORS Support

For cross-origin requests, configure CORS settings in your Django settings.

## SDK Examples

### Python Example
```python
import requests

# Authentication
token_response = requests.post('https://your-domain.com/api/auth/token/', {
    'username': 'your_username',
    'password': 'your_password'
})
token = token_response.json()['token']

# Make authenticated request
headers = {'Authorization': f'Token {token}'}
response = requests.get('https://your-domain.com/api/samples/123/tree/', headers=headers)
sample_tree = response.json()
print(sample_tree)
```

### JavaScript Example
```javascript
// Authentication
fetch('https://your-domain.com/api/auth/token/', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({username: 'your_username', password: 'your_password'})
})
.then(response => response.json())
.then(data => {
  const token = data.token;
  
  // Make authenticated request
  return fetch('https://your-domain.com/api/samples/123/tree/', {
    headers: {'Authorization': `Token ${token}`}
  });
})
.then(response => response.json())
.then(sampleTree => console.log(sampleTree));
```

## Support

For technical support or questions about the API, please contact the development team.

---

*Last updated: July 2025*