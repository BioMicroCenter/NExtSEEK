# NExtSEEK API Migration Guide

## Overview

This guide helps you migrate from the old `api_app` endpoints to the new `nextseek_api` REST API. The new API provides improved authentication, better error handling, pagination, and comprehensive OpenAPI documentation.

## Migration Timeline

- **Phase 1**: New API available alongside legacy endpoints ✅ **CURRENT**
- **Phase 2**: Deprecation warnings for legacy endpoints (6 months)
- **Phase 3**: Legacy endpoint removal (12 months)

## Key Improvements in New API

### ✅ **Security Enhancements**
- **Proper Authentication**: ViewSet-level authentication (no more decorator bypass issues)
- **Permission Classes**: `IsAuthenticated` and `IsAdminUser` properly enforced
- **Token Authentication**: Secure API token support

### ✅ **Performance Improvements**
- **Pagination**: Large datasets automatically paginated (100 items per page)
- **Response Optimization**: Proper DRF Response objects with content negotiation
- **Database Efficiency**: Optimized query patterns

### ✅ **Developer Experience**
- **OpenAPI 3.0**: Complete interactive documentation at `/api/swagger/`
- **Proper Error Handling**: Consistent HTTP status codes and error messages
- **Content-Type Headers**: Correct MIME types for Excel downloads and JSON responses

## Endpoint Migration Map

### 1. Sample Tree Endpoints

#### **Legacy → New API**

| **Legacy Endpoint** | **New API Endpoint** | **Changes** |
|---------------------|----------------------|-------------|
| `/legacy/sampleTreeNew/{id}/` | `/api/samples/{id}/tree/` | ✅ Proper authentication<br/>✅ Better error handling<br/>✅ OpenAPI documented |
| `/legacy/sampleTreeNewUID/{uuid}/` | `/api/samples-uuid/{uuid}/tree/` | ✅ Separate endpoint to avoid routing conflicts<br/>✅ UUID validation<br/>✅ Consistent response format |

#### **Migration Example:**
```bash
# OLD (legacy)
curl "https://your-domain.com/legacy/sampleTreeNew/123/"

# NEW (recommended)
curl -H "Authorization: Token your_token" \
  "https://your-domain.com/api/samples/123/tree/"
```

**⚠️ Breaking Changes:**
- **Authentication now required** - all requests must include valid token
- **Response format unchanged** - same JSON structure maintained

### 2. NHP (Non-Human Primate) Endpoints

#### **Legacy → New API**

| **Legacy Endpoint** | **New API Endpoint** | **Changes** |
|---------------------|----------------------|-------------|
| `/legacy/nhp_info/{nhp_name}/` | `/api/nhp/{nhp_name}/info/` | ✅ Proper authentication<br/>✅ 404 handling for missing NHPs<br/>✅ OpenAPI documented |
| `/legacy/fetch_event_data/{nhp_name}/{event_type}/{date}/` | `/api/nhp/{nhp_name}/events/{event_type}/{date}/` | ✅ Cleaner URL structure<br/>✅ Parameter validation<br/>✅ Better error messages |
| `/legacy/get_nhp_data/{nhp_name}/timeline/` | `/api/nhp/{nhp_name}/timeline/` | ✅ Simplified URL pattern<br/>✅ Consistent response format |
| `/legacy/download_nhp_data/{nhp_name}/` | `/api/nhp/{nhp_name}/download/` | ✅ Proper Content-Type headers<br/>✅ FileResponse handling<br/>✅ Attachment disposition |

#### **Migration Example:**
```bash
# OLD (legacy)
curl "https://your-domain.com/legacy/nhp_info/FLY001/"

# NEW (recommended)
curl -H "Authorization: Token your_token" \
  "https://your-domain.com/api/nhp/FLY001/info/"
```

**⚠️ Breaking Changes:**
- **Authentication now required** for all NHP endpoints
- **Excel downloads** now return proper `Content-Type` headers
- **404 errors** for non-existent NHP names (instead of empty responses)

### 3. Sample Query Endpoints

#### **Legacy → New API**

| **Legacy Endpoint** | **New API Endpoint** | **Changes** |
|---------------------|----------------------|-------------|
| `/legacy/retrieveSamples/` | `/api/sample-queries/retrieve-samples/` | ✅ **PAGINATION ADDED**<br/>✅ Query parameters support<br/>✅ Proper JSON responses |
| `/legacy/adminRetrieveSamples/` | `/api/admin/samples/admin-retrieve-samples/` | ✅ **Admin permissions enforced**<br/>✅ 403 Forbidden for non-admin users<br/>✅ Separate admin endpoint |

#### **Migration Example:**
```bash
# OLD (legacy) - could return massive datasets
curl "https://your-domain.com/legacy/retrieveSamples/"

# NEW (recommended) - paginated results
curl -H "Authorization: Token your_token" \
  "https://your-domain.com/api/sample-queries/retrieve-samples/?page=1&page_size=100"
```

**🚨 Critical Breaking Changes:**
- **Pagination required** - large datasets now paginated (default: 100 items per page)
- **Admin endpoints moved** - separate URL pattern with proper permission checks
- **Response format changed** - wrapped in pagination envelope:
  ```json
  {
    "count": 1500,
    "next": "https://domain.com/api/sample-queries/retrieve-samples/?page=2",
    "previous": null,
    "results": [/* your data here */]
  }
  ```

## Authentication Migration

### Old API (Insecure)
```bash
# No authentication required (security issue)
curl "https://your-domain.com/legacy/sampleTreeNew/123/"
```

### New API (Secure)
```bash
# Step 1: Get authentication token
curl -X POST "https://your-domain.com/api/auth/token/" \
  -H "Content-Type: application/json" \
  -d '{"username": "your_username", "password": "your_password"}'

# Step 2: Use token in requests
curl -H "Authorization: Token your_token_here" \
  "https://your-domain.com/api/samples/123/tree/"
```

## Code Migration Examples

### Python Client Migration

#### **Before (Legacy):**
```python
import requests

# No authentication needed (insecure)
response = requests.get('https://your-domain.com/legacy/sampleTreeNew/123/')
data = response.json()

# No pagination handling
samples_response = requests.get('https://your-domain.com/legacy/retrieveSamples/')
all_samples = samples_response.json()  # Could be massive!
```

#### **After (New API):**
```python
import requests

class NExtSEEKAPIClient:
    def __init__(self, base_url, username, password):
        self.base_url = base_url
        self.token = self._get_token(username, password)
    
    def _get_token(self, username, password):
        response = requests.post(f'{self.base_url}/api/auth/token/', {
            'username': username, 'password': password
        })
        return response.json()['token']
    
    @property
    def headers(self):
        return {'Authorization': f'Token {self.token}'}
    
    def get_sample_tree(self, sample_id):
        response = requests.get(
            f'{self.base_url}/api/samples/{sample_id}/tree/',
            headers=self.headers
        )
        return response.json()
    
    def get_samples_paginated(self, page=1, page_size=100):
        response = requests.get(
            f'{self.base_url}/api/sample-queries/retrieve-samples/',
            params={'page': page, 'page_size': page_size},
            headers=self.headers
        )
        return response.json()
    
    def get_all_samples(self):
        """Handle pagination automatically"""
        all_samples = []
        page = 1
        
        while True:
            response = self.get_samples_paginated(page=page)
            all_samples.extend(response['results'])
            
            if not response['next']:
                break
            page += 1
        
        return all_samples

# Usage
client = NExtSEEKAPIClient('https://your-domain.com', 'username', 'password')
sample_tree = client.get_sample_tree(123)
all_samples = client.get_all_samples()  # Handles pagination automatically
```

### JavaScript Client Migration

#### **Before (Legacy):**
```javascript
// No authentication (insecure)
fetch('https://your-domain.com/legacy/sampleTreeNew/123/')
  .then(response => response.json())
  .then(data => console.log(data));
```

#### **After (New API):**
```javascript
class NExtSEEKAPI {
  constructor(baseUrl, username, password) {
    this.baseUrl = baseUrl;
    this.tokenPromise = this.getToken(username, password);
  }
  
  async getToken(username, password) {
    const response = await fetch(`${this.baseUrl}/api/auth/token/`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username, password})
    });
    const data = await response.json();
    return data.token;
  }
  
  async request(endpoint, options = {}) {
    const token = await this.tokenPromise;
    const defaultOptions = {
      headers: {
        'Authorization': `Token ${token}`,
        'Content-Type': 'application/json',
        ...options.headers
      }
    };
    
    return fetch(`${this.baseUrl}${endpoint}`, {...defaultOptions, ...options});
  }
  
  async getSampleTree(sampleId) {
    const response = await this.request(`/api/samples/${sampleId}/tree/`);
    return response.json();
  }
  
  async getSamplesPaginated(page = 1, pageSize = 100) {
    const response = await this.request(
      `/api/sample-queries/retrieve-samples/?page=${page}&page_size=${pageSize}`
    );
    return response.json();
  }
}

// Usage
const api = new NExtSEEKAPI('https://your-domain.com', 'username', 'password');
const sampleTree = await api.getSampleTree(123);
```

## Error Handling Migration

### Old API (Inconsistent)
```python
response = requests.get('/legacy/endpoint/')
# Errors returned as HTML or inconsistent JSON
if response.status_code == 200:
    data = response.json()  # Might fail with HTML error pages
```

### New API (Consistent)
```python
response = requests.get('/api/endpoint/', headers=headers)

if response.status_code == 200:
    data = response.json()
elif response.status_code == 401:
    print("Authentication required")
elif response.status_code == 403:
    print("Insufficient permissions") 
elif response.status_code == 404:
    error = response.json()
    print(f"Not found: {error['detail']}")
else:
    error = response.json()
    print(f"API Error: {error['detail']}")
```

## Testing Your Migration

### 1. Verify Authentication
```bash
# Test token generation
curl -X POST "https://your-domain.com/api/auth/token/" \
  -H "Content-Type: application/json" \
  -d '{"username": "test_user", "password": "test_password"}'
```

### 2. Test Core Endpoints
```bash
# Test sample tree endpoint
curl -H "Authorization: Token YOUR_TOKEN" \
  "https://your-domain.com/api/samples/123/tree/"

# Test pagination
curl -H "Authorization: Token YOUR_TOKEN" \
  "https://your-domain.com/api/sample-queries/retrieve-samples/?page=1&page_size=10"
```

### 3. Verify Error Handling
```bash
# Test without authentication (should return 401)
curl "https://your-domain.com/api/samples/123/tree/"

# Test with invalid ID (should return 404)  
curl -H "Authorization: Token YOUR_TOKEN" \
  "https://your-domain.com/api/samples/999999/tree/"
```

## Migration Checklist

### ✅ **Development Phase**
- [ ] Update client code to use new authentication
- [ ] Implement pagination handling for large datasets
- [ ] Update error handling for consistent HTTP status codes
- [ ] Test all endpoints with proper authentication
- [ ] Verify Excel download Content-Type headers

### ✅ **Testing Phase**
- [ ] Run comprehensive test suite
- [ ] Performance testing with pagination
- [ ] Security testing (authentication bypass attempts)
- [ ] Load testing for high-traffic endpoints

### ✅ **Deployment Phase**
- [ ] Deploy new API alongside existing legacy endpoints
- [ ] Update client applications gradually
- [ ] Monitor API usage and error rates
- [ ] Document any custom migration requirements

## Support and Troubleshooting

### Common Migration Issues

1. **Authentication Errors (401)**
   - Verify token generation endpoint
   - Check token format in Authorization header
   - Ensure tokens haven't expired

2. **Permission Errors (403)**
   - Verify user has required permissions
   - Check if endpoint requires admin privileges
   - Confirm user account is active

3. **Pagination Confusion**
   - Update clients to handle paginated responses
   - Implement automatic pagination iteration
   - Set appropriate page_size for performance

4. **Content-Type Issues**
   - Excel downloads now have proper MIME types
   - JSON responses include proper headers
   - Update client content-type handling

### Getting Help

- **Interactive Documentation**: https://your-domain.com/api/swagger/
- **API Schema**: https://your-domain.com/api/schema/
- **Technical Support**: Contact development team

---

**Migration Timeline**: Start migration as soon as possible. Legacy endpoints will be deprecated in 6 months and removed in 12 months.

*Last updated: July 2025*