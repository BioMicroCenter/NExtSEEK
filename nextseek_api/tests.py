import json
import io
from unittest.mock import patch, Mock, MagicMock
from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from rest_framework.authtoken.models import Token


class SampleTreeViewSetTests(APITestCase):
    """
    Test cases for SampleTreeByIDViewSet and SampleTreeByUUIDViewSet
    Covers Step 6.1 (Positive) and Step 6.2 (Negative) testing
    """
    
    def setUp(self):
        """Set up test data and authentication"""
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        self.admin_user = User.objects.create_superuser(
            username='admin',
            password='adminpass123'
        )
        self.token = Token.objects.create(user=self.user)
        self.admin_token = Token.objects.create(user=self.admin_user)
        self.client = APIClient()
    
    @patch('nextseek_api.views.GraphDatabase.driver')
    @patch('nextseek_api.views.SeekDB')
    @patch('nextseek_api.views.get_clade_color')
    def test_sample_tree_by_id_success(self, mock_color, mock_seekdb, mock_driver):
        """Test successful sample tree retrieval by numeric ID"""
        # Mock authentication
        mock_seekdb.return_value.getSeekLogin.return_value = {'status': True}
        
        # Mock Neo4j response
        mock_node = Mock()
        mock_node._properties = {
            'uuid': 'test-uuid-123',
            'id': 123,
            'type': 'Sample'
        }
        
        mock_relationship = Mock()
        mock_relationship.start_node._properties = {
            'uuid': 'test-uuid-123',
            'id': 123,
            'type': 'Sample'
        }
        mock_relationship.end_node._properties = {'id': 456}
        
        mock_result = Mock()
        mock_result.nodes = [mock_node]
        mock_result.relationships = [mock_relationship]
        
        mock_driver.return_value.__enter__.return_value.execute_query.return_value = mock_result
        mock_color.return_value = '#FF0000'
        
        # Test authenticated request
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:samples-by-id-tree', kwargs={'pk': 123})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        
        # Verify response structure
        if response.data:
            node_data = response.data[0]
            self.assertIn('id', node_data)
            self.assertIn('uuid', node_data)
            self.assertIn('type', node_data)
            self.assertIn('color', node_data)
            self.assertIn('parentIds', node_data)
    
    def test_sample_tree_by_id_unauthorized(self):
        """Test 401 Unauthorized for sample tree by ID without auth"""
        url = reverse('nextseek_api:samples-by-id-tree', kwargs={'pk': 123})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_sample_tree_by_id_unauthorized(self):
        """Test 401 Unauthorized for sample tree by ID without auth"""
        url = reverse('nextseek_api:samples-by-id-tree', kwargs={'pk': 123})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    @patch('nextseek_api.views.SeekDB')
    def test_sample_tree_by_id_authentication_failed(self, mock_seekdb):
        """Test authentication failure handling"""
        # Mock failed authentication
        mock_seekdb.return_value.getSeekLogin.return_value = {'status': False}
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:samples-by-id-tree', kwargs={'pk': 123})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn('detail', response.data)
        self.assertEqual(response.data['detail'], 'Authentication required')


class NHPViewSetTests(APITestCase):
    """
    Test cases for NHPViewSet - covers all 4 actions
    """
    
    def setUp(self):
        """Set up test data and authentication"""
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
    
    @patch('nextseek_api.views.save_nhp_info_to_json')
    def test_nhp_info_success(self, mock_save_nhp):
        """Test successful NHP info retrieval"""
        mock_save_nhp.return_value = {'id': 'FLY001', 'metadata': {'test': 'data'}}
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:nhp-info', kwargs={'pk': 'FLY001'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], 'FLY001')
    
    @patch('nextseek_api.views.save_nhp_info_to_json')
    def test_nhp_info_not_found(self, mock_save_nhp):
        """Test 404 for non-existent NHP"""
        mock_save_nhp.return_value = None
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:nhp-info', kwargs={'pk': 'NONEXISTENT'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('detail', response.data)
    
    @patch('nextseek_api.views.save_nhp_data')
    @patch('nextseek_api.views.get_timeline_data')
    def test_nhp_download_excel_content_type(self, mock_get_timeline, mock_save_data):
        """Test successful NHP Excel download with correct Content-Type"""
        mock_get_timeline.return_value = {'data': 'test'}
        mock_save_data.return_value = b'fake excel data'
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:nhp-download', kwargs={'pk': 'FLY001'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Check Content-Type for Excel file (Step 6.4: Content Negotiation)
        self.assertEqual(
            response.get('Content-Type'),
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    
    def test_nhp_unauthorized_access(self):
        """Test 401 Unauthorized for NHP endpoints without auth"""
        url = reverse('nextseek_api:nhp-info', kwargs={'pk': 'FLY001'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn('detail', response.data)
        self.assertEqual(response.data['detail'], 'Authentication required')


class NHPViewSetTests(APITestCase):
    """
    Test cases for NHPViewSet - covers all 4 actions
    """
    
    def setUp(self):
        """Set up test data and authentication"""
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
    
    @patch('nextseek_api.views.save_nhp_info_to_json')
    def test_nhp_info_success(self, mock_save_nhp):
        """Test successful NHP info retrieval"""
        mock_save_nhp.return_value = {'id': 'FLY001', 'metadata': {'test': 'data'}}
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:nhp-info', kwargs={'pk': 'FLY001'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], 'FLY001')
    
    @patch('nextseek_api.views.save_nhp_info_to_json')
    def test_nhp_info_not_found(self, mock_save_nhp):
        """Test 404 for non-existent NHP"""
        mock_save_nhp.return_value = None
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:nhp-info', kwargs={'pk': 'NONEXISTENT'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('detail', response.data)
    
    @patch('nextseek_api.views.save_nhp_data')
    @patch('nextseek_api.views.get_timeline_data')
    def test_nhp_download_excel_content_type(self, mock_get_timeline, mock_save_data):
        """Test successful NHP Excel download with correct Content-Type"""
        mock_get_timeline.return_value = {'data': 'test'}
        mock_save_data.return_value = b'fake excel data'
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:nhp-download', kwargs={'pk': 'FLY001'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Check Content-Type for Excel file (Step 6.4: Content Negotiation)
        self.assertEqual(
            response.get('Content-Type'),
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    
    def test_nhp_unauthorized_access(self):
        """Test 401 Unauthorized for NHP endpoints without auth"""
        url = reverse('nextseek_api:nhp-info', kwargs={'pk': 'FLY001'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_nhp_unauthorized_access(self):
        """Test 401 Unauthorized for NHP endpoints without auth"""
        url = reverse('nextseek_api:nhp-info', kwargs={'pk': 'FLY001'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SampleQueryViewSetTests(APITestCase):
    """
    Test cases for SampleQueryViewSet with pagination
    Covers Step 6.3 (Pagination) testing
    """
    
    def setUp(self):
        """Set up test data and authentication"""
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
    
    @patch('nextseek_api.views.DBtable_sample')
    @patch('nextseek_api.views.SeekDB')
    def test_sample_query_with_pagination(self, mock_seekdb, mock_db_sample):
        """Test paginated sample query response"""
        # Mock authentication
        mock_seekdb.return_value.getSeekLogin.return_value = {'status': True}
        
        # Mock large dataset (200 items to test pagination)
        mock_data = [{'id': i, 'title': f'Sample {i}'} for i in range(200)]
        mock_db_sample.return_value.processRecords.return_value = json.dumps(mock_data)
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sample-queries-retrieve-samples')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify pagination structure
        self.assertIn('count', response.data)
        self.assertIn('next', response.data)
        self.assertIn('previous', response.data)
        self.assertIn('results', response.data)
        
        # Verify pagination limits (should be 100 per page by default)
        self.assertEqual(len(response.data['results']), 100)
        self.assertEqual(response.data['count'], 200)
    
    @patch('nextseek_api.views.DBtable_sample')
    @patch('nextseek_api.views.SeekDB')
    def test_sample_query_custom_page_size(self, mock_seekdb, mock_db_sample):
        """Test custom page size parameter"""
        # Mock authentication and data
        mock_seekdb.return_value.getSeekLogin.return_value = {'status': True}
        mock_data = [{'id': i, 'title': f'Sample {i}'} for i in range(50)]
        mock_db_sample.return_value.processRecords.return_value = json.dumps(mock_data)
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sample-queries-retrieve-samples')
        response = self.client.get(url, {'page_size': 25})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 25)
    
    def test_sample_tree_by_id_unauthorized(self):
        """Test 401 Unauthorized for sample tree by ID without auth"""
        url = reverse('nextseek_api:samples-by-id-tree', kwargs={'pk': 123})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    @patch('nextseek_api.views.SeekDB')
    def test_sample_tree_by_id_authentication_failed(self, mock_seekdb):
        """Test authentication failure handling"""
        # Mock failed authentication
        mock_seekdb.return_value.getSeekLogin.return_value = {'status': False}
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:samples-by-id-tree', kwargs={'pk': 123})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn('detail', response.data)
        self.assertEqual(response.data['detail'], 'Authentication required')


class NHPViewSetTests(APITestCase):
    """
    Test cases for NHPViewSet - covers all 4 actions
    """
    
    def setUp(self):
        """Set up test data and authentication"""
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
    
    @patch('nextseek_api.views.save_nhp_info_to_json')
    def test_nhp_info_success(self, mock_save_nhp):
        """Test successful NHP info retrieval"""
        mock_save_nhp.return_value = {'id': 'FLY001', 'metadata': {'test': 'data'}}
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:nhp-info', kwargs={'pk': 'FLY001'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], 'FLY001')
    
    @patch('nextseek_api.views.save_nhp_info_to_json')
    def test_nhp_info_not_found(self, mock_save_nhp):
        """Test 404 for non-existent NHP"""
        mock_save_nhp.return_value = None
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:nhp-info', kwargs={'pk': 'NONEXISTENT'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('detail', response.data)
    
    @patch('nextseek_api.views.save_nhp_data')
    @patch('nextseek_api.views.get_timeline_data')
    def test_nhp_download_excel_content_type(self, mock_get_timeline, mock_save_data):
        """Test successful NHP Excel download with correct Content-Type"""
        mock_get_timeline.return_value = {'data': 'test'}
        mock_save_data.return_value = b'fake excel data'
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:nhp-download', kwargs={'pk': 'FLY001'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Check Content-Type for Excel file (Step 6.4: Content Negotiation)
        self.assertEqual(
            response.get('Content-Type'),
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    
    def test_nhp_unauthorized_access(self):
        """Test 401 Unauthorized for NHP endpoints without auth"""
        url = reverse('nextseek_api:nhp-info', kwargs={'pk': 'FLY001'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SampleQueryViewSetTests(APITestCase):
    """
    Test cases for SampleQueryViewSet with pagination
    Covers Step 6.3 (Pagination) testing
    """
    
    def setUp(self):
        """Set up test data and authentication"""
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
    
    @patch('nextseek_api.views.DBtable_sample')
    @patch('nextseek_api.views.SeekDB')
    def test_sample_query_with_pagination(self, mock_seekdb, mock_db_sample):
        """Test paginated sample query response"""
        # Mock authentication
        mock_seekdb.return_value.getSeekLogin.return_value = {'status': True}
        
        # Mock large dataset (200 items to test pagination)
        mock_data = [{'id': i, 'title': f'Sample {i}'} for i in range(200)]
        mock_db_sample.return_value.processRecords.return_value = json.dumps(mock_data)
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sample-queries-retrieve-samples')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify pagination structure
        self.assertIn('count', response.data)
        self.assertIn('next', response.data)
        self.assertIn('previous', response.data)
        self.assertIn('results', response.data)
        
        # Verify pagination limits (should be 100 per page by default)
        self.assertEqual(len(response.data['results']), 100)
        self.assertEqual(response.data['count'], 200)
    
    @patch('nextseek_api.views.DBtable_sample')
    @patch('nextseek_api.views.SeekDB')
    def test_sample_query_custom_page_size(self, mock_seekdb, mock_db_sample):
        """Test custom page size parameter"""
        # Mock authentication and data
        mock_seekdb.return_value.getSeekLogin.return_value = {'status': True}
        mock_data = [{'id': i, 'title': f'Sample {i}'} for i in range(50)]
        mock_db_sample.return_value.processRecords.return_value = json.dumps(mock_data)
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sample-queries-retrieve-samples')
        response = self.client.get(url, {'page_size': 25})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 25)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 25)


class AdminSampleViewSetTests(APITestCase):
    """
    Test cases for AdminSampleViewSet
    Covers Step 6.2 (Negative) permission testing
    """
    
    def setUp(self):
        """Set up test data and authentication"""
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.admin_user = User.objects.create_superuser(username='admin', password='adminpass123')
        
        self.user_token = Token.objects.create(user=self.user)
        self.admin_token = Token.objects.create(user=self.admin_user)
        self.client = APIClient()
    
    @patch('nextseek_api.views.DBtable_sample')
    @patch('nextseek_api.views.SeekDB')
    def test_admin_sample_query_success(self, mock_seekdb, mock_db_sample):
        """Test successful admin sample query with admin user"""
        # Mock authentication and data
        mock_seekdb.return_value.getSeekLogin.return_value = {'status': True}
        mock_db_sample.return_value.processRecords.return_value = json.dumps([{'id': 1}])
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.admin_token.key)
        url = reverse('nextseek_api:admin-samples-admin-retrieve-samples')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_admin_sample_query_forbidden(self):
        """Test 403 Forbidden for non-admin user accessing admin endpoint"""
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user_token.key)
        url = reverse('nextseek_api:admin-samples-admin-retrieve-samples')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
    
    def test_admin_sample_query_unauthorized(self):
        """Test 401 Unauthorized for admin endpoint without auth"""
        url = reverse('nextseek_api:admin-samples-admin-retrieve-samples')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ErrorHandlingTests(APITestCase):
    """
    Test cases for Step 6.2 (Negative) error scenarios
    """
    
    def setUp(self):
        """Set up test data and authentication"""
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
    
    @patch('nextseek_api.views.save_nhp_info_to_json')
    def test_internal_server_error_handling(self, mock_save_nhp):
        """Test 500 Internal Server Error handling"""
        # Mock an exception in the service layer
        mock_save_nhp.side_effect = Exception("Database connection failed")
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:nhp-info', kwargs={'pk': 'FLY001'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn('detail', response.data)
        self.assertEqual(response.data['detail'], "Database connection failed")
    
    def test_404_not_found_invalid_endpoint(self):
        """Test 404 for invalid endpoints"""
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        response = self.client.get('/api/nonexistent/endpoint/')
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class NHPViewSetTests(APITestCase):
    """
    Test cases for NHPViewSet - covers all 4 actions
    """
    
    def setUp(self):
        """Set up test data and authentication"""
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
    
    @patch('nextseek_api.views.save_nhp_info_to_json')
    def test_nhp_info_success(self, mock_save_nhp):
        """Test successful NHP info retrieval"""
        mock_save_nhp.return_value = {'id': 'FLY001', 'metadata': {'test': 'data'}}
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:nhp-info', kwargs={'pk': 'FLY001'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], 'FLY001')
    
    @patch('nextseek_api.views.save_nhp_info_to_json')
    def test_nhp_info_not_found(self, mock_save_nhp):
        """Test 404 for non-existent NHP"""
        mock_save_nhp.return_value = None
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:nhp-info', kwargs={'pk': 'NONEXISTENT'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('detail', response.data)
    
    @patch('nextseek_api.views.save_nhp_data')
    @patch('nextseek_api.views.get_timeline_data')
    def test_nhp_download_excel_content_type(self, mock_get_timeline, mock_save_data):
        """Test successful NHP Excel download with correct Content-Type"""
        mock_get_timeline.return_value = {'data': 'test'}
        mock_save_data.return_value = b'fake excel data'
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:nhp-download', kwargs={'pk': 'FLY001'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Check Content-Type for Excel file (Step 6.4: Content Negotiation)
        self.assertEqual(
            response.get('Content-Type'),
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    
    def test_nhp_unauthorized_access(self):
        """Test 401 Unauthorized for NHP endpoints without auth"""
        url = reverse('nextseek_api:nhp-info', kwargs={'pk': 'FLY001'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SampleQueryViewSetTests(APITestCase):
    """
    Test cases for SampleQueryViewSet with pagination
    Covers Step 6.3 (Pagination) testing
    """
    
    def setUp(self):
        """Set up test data and authentication"""
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
    
    @patch('nextseek_api.views.DBtable_sample')
    @patch('nextseek_api.views.SeekDB')
    def test_sample_query_with_pagination(self, mock_seekdb, mock_db_sample):
        """Test paginated sample query response"""
        # Mock authentication
        mock_seekdb.return_value.getSeekLogin.return_value = {'status': True}
        
        # Mock large dataset (200 items to test pagination)
        mock_data = [{'id': i, 'title': f'Sample {i}'} for i in range(200)]
        mock_db_sample.return_value.processRecords.return_value = json.dumps(mock_data)
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sample-queries-retrieve-samples')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify pagination structure
        self.assertIn('count', response.data)
        self.assertIn('next', response.data)
        self.assertIn('previous', response.data)
        self.assertIn('results', response.data)
        
        # Verify pagination limits (should be 100 per page by default)
        self.assertEqual(len(response.data['results']), 100)
        self.assertEqual(response.data['count'], 200)
    
    @patch('nextseek_api.views.DBtable_sample')
    @patch('nextseek_api.views.SeekDB')
    def test_sample_query_custom_page_size(self, mock_seekdb, mock_db_sample):
        """Test custom page size parameter"""
        # Mock authentication and data
        mock_seekdb.return_value.getSeekLogin.return_value = {'status': True}
        mock_data = [{'id': i, 'title': f'Sample {i}'} for i in range(50)]
        mock_db_sample.return_value.processRecords.return_value = json.dumps(mock_data)
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sample-queries-retrieve-samples')
        response = self.client.get(url, {'page_size': 25})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 25)


class AdminSampleViewSetTests(APITestCase):
    """
    Test cases for AdminSampleViewSet
    Covers Step 6.2 (Negative) permission testing
    """
    
    def setUp(self):
        """Set up test data and authentication"""
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.admin_user = User.objects.create_superuser(username='admin', password='adminpass123')
        
        self.user_token = Token.objects.create(user=self.user)
        self.admin_token = Token.objects.create(user=self.admin_user)
        self.client = APIClient()
    
    @patch('nextseek_api.views.DBtable_sample')
    @patch('nextseek_api.views.SeekDB')
    def test_admin_sample_query_success(self, mock_seekdb, mock_db_sample):
        """Test successful admin sample query with admin user"""
        # Mock authentication and data
        mock_seekdb.return_value.getSeekLogin.return_value = {'status': True}
        mock_db_sample.return_value.processRecords.return_value = json.dumps([{'id': 1}])
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.admin_token.key)
        url = reverse('nextseek_api:admin-samples-admin-retrieve-samples')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_admin_sample_query_forbidden(self):
        """Test 403 Forbidden for non-admin user accessing admin endpoint"""
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user_token.key)
        url = reverse('nextseek_api:admin-samples-admin-retrieve-samples')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
    
    def test_admin_sample_query_unauthorized(self):
        """Test 401 Unauthorized for admin endpoint without auth"""
        url = reverse('nextseek_api:admin-samples-admin-retrieve-samples')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ErrorHandlingTests(APITestCase):
    """
    Test cases for Step 6.2 (Negative) error scenarios
    """
    
    def setUp(self):
        """Set up test data and authentication"""
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
    
    @patch('nextseek_api.views.save_nhp_info_to_json')
    def test_internal_server_error_handling(self, mock_save_nhp):
        """Test 500 Internal Server Error handling"""
        # Mock an exception in the service layer
        mock_save_nhp.side_effect = Exception("Database connection failed")
        
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:nhp-info', kwargs={'pk': 'FLY001'})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn('detail', response.data)
        self.assertEqual(response.data['detail'], "Database connection failed")
    
    def test_404_not_found_invalid_endpoint(self):
        """Test 404 for invalid endpoints"""
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        response = self.client.get('/api/nonexistent/endpoint/')
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)



class SopProxyViewSetTests(APITestCase):
    """Minimal tests for SOP proxy endpoints covering 200/401/404/422/502 paths."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()

    def _good_list_payload(self):
        return {
            "data": [
                {
                    "id": "131",
                    "type": "sops",
                    "attributes": {"title": "This Sop"},
                    "links": {"self": "/sops/131"}
                }
            ],
            "jsonapi": {"version": "1.0"},
            "links": {"self": "/sops?page[number]=1&page[size]=100"},
            "meta": {"base_url": "http://localhost:3000", "api_version": "v1"}
        }

    def _good_single_payload(self, sop_id="132"):
        return {
            "data": {
                "id": sop_id,
                "type": "sops",
                "attributes": {"title": "A Maximal SOP"},
                "relationships": {
                    "creators": {"data": []},
                    "submitter": {"data": []},
                    "people": {"data": []},
                    "projects": {"data": []},
                    "investigations": {"data": []},
                    "studies": {"data": []},
                    "assays": {"data": []},
                    "publications": {"data": []},
                    "workflows": {"data": []}
                },
                "links": {"self": f"/sops/{sop_id}"},
                "meta": {}
            },
            "jsonapi": {"version": "1.0"}
        }

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_sops_list_200(self, _auth):
        from nextseek_api.services.sops import SopProxyViewSet
        # Mock upstream JSON
        SopProxyViewSet.client.list_sops = Mock(return_value=(
            json.dumps(self._good_list_payload()).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sops-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=None)
    def test_sops_list_401(self, _auth):
        url = reverse('nextseek_api:sops-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_sops_list_502_html(self, _auth):
        from nextseek_api.services.sops import SopProxyViewSet
        SopProxyViewSet.client.list_sops = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sops-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_sops_retrieve_200_numeric_uid(self, _auth):
        from nextseek_api.services.sops import SopProxyViewSet
        SopProxyViewSet.client.get_sop = Mock(return_value=(
            json.dumps(self._good_single_payload("123")).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sops-detail', kwargs={'uid': '123'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    @patch('nextseek_api.services.sops.DBtable_sops')
    def test_sops_retrieve_404_string_uid_not_found(self, mock_dbsop, _auth):
        # No matching title → None
        mock_dbsop.return_value.queryRecordsByConstraint.return_value = []
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sops-detail', kwargs={'uid': 'NotThere.pdf'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_sops_retrieve_502_html(self, _auth):
        from nextseek_api.services.sops import SopProxyViewSet
        SopProxyViewSet.client.get_sop = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sops-detail', kwargs={'uid': '123'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_sops_create_201(self, _auth):
        from nextseek_api.services.sops import SopProxyViewSet
        SopProxyViewSet.client.create_sop = Mock(return_value=(
            json.dumps(self._good_single_payload("140")).encode(), 201, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sops-list')
        payload = {
            "data": {
                "type": "sops",
                "attributes": {
                    "title": "A Maximal SOP",
                    "content_blobs": [{"original_filename": "a.pdf", "content_type": "application/pdf"}]
                },
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}}
            }
        }
        resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertIn(resp.status_code, (status.HTTP_201_CREATED, status.HTTP_200_OK))

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_sops_create_422_invalid_body(self, _auth):
        # Missing required fields (title/content_blobs/projects)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sops-list')
        bad_payload = {"data": {"type": "sops", "attributes": {}, "relationships": {}}}
        resp = self.client.post(url, data=json.dumps(bad_payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_sops_patch_200(self, _auth):
        from nextseek_api.services.sops import SopProxyViewSet
        SopProxyViewSet.client.update_sop = Mock(return_value=(
            json.dumps(self._good_single_payload("132")).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sops-detail', kwargs={'uid': '132'})
        payload = {"data": {"type": "sops", "id": "132", "attributes": {"title": "Patched"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_sops_patch_404_no_resolution(self, _auth):
        # No id in payload and non-numeric uid that won't be resolved (DB not patched returns None)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sops-detail', kwargs={'uid': 'NotThere.pdf'})
        payload = {"data": {"type": "sops", "attributes": {"title": "Patched"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch('nextseek_api.helpers.get_auth', return_value=None)
    def test_sops_patch_401_no_auth(self, _auth):
        url = reverse('nextseek_api:sops-detail', kwargs={'uid': '132'})
        payload = {"data": {"type": "sops", "id": "132", "attributes": {"title": "Patched"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_sops_patch_502_html(self, _auth):
        from nextseek_api.services.sops import SopProxyViewSet
        SopProxyViewSet.client.update_sop = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:sops-detail', kwargs={'uid': '132'})
        payload = {"data": {"type": "sops", "id": "132", "attributes": {"title": "Patched"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)


class DataFileProxyViewSetTests(APITestCase):
    """Minimal tests for DataFile proxy endpoints covering 200/401/404/422/502 paths."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()

    def _good_index_payload(self):
        return {
            "data": [
                {
                    "id": "560",
                    "type": "data_files",
                    "attributes": {"title": "DF-20240101-01_Sample-X.csv"},
                    "links": {"self": "/data_files/560"}
                }
            ],
            "jsonapi": {"version": "1.0"},
            "links": {"self": "/data_files?page[number]=1&page[size]=100"},
            "meta": {"base_url": "http://localhost:3000", "api_version": "v1"}
        }

    def _good_single_payload(self, df_id="560"):
        return {
            "data": {
                "id": df_id,
                "type": "data_files",
                "attributes": {"title": "DF-20240101-01_Sample-X.csv"},
                "relationships": {},
                "links": {"self": f"/data_files/{df_id}"},
                "meta": {}
            },
            "jsonapi": {"version": "1.0"}
        }

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_data_files_list_200(self, _auth):
        from nextseek_api.services.data_files import DataFileProxyViewSet
        DataFileProxyViewSet.client.list_data_files = Mock(return_value=(
            json.dumps(self._good_index_payload()).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:data_files-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=None)
    def test_data_files_list_401(self, _auth):
        url = reverse('nextseek_api:data_files-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_data_files_list_502_html(self, _auth):
        from nextseek_api.services.data_files import DataFileProxyViewSet
        DataFileProxyViewSet.client.list_data_files = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:data_files-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_data_files_retrieve_422_missing_version(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:data_files-detail', kwargs={'uid': '560'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @patch('nextseek_api.services.data_files.DBtable_data_files')
    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_data_files_retrieve_200_with_version(self, _auth, mock_dbdf):
        from nextseek_api.services.data_files import DataFileProxyViewSet
        # ensure uid resolution returns same numeric id
        mock_dbdf.return_value.queryRecordsByConstraint.return_value = [{'id': 560}]
        DataFileProxyViewSet.client.get_data_file = Mock(return_value=(
            json.dumps(self._good_single_payload("560")).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:data_files-detail', kwargs={'uid': 'DF-20240101-01_Sample-X.csv'})
        resp = self.client.get(url, {'version': 1})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.services.data_files.DBtable_data_files')
    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_data_files_retrieve_404_unresolved_uid(self, _auth, mock_dbdf):
        mock_dbdf.return_value.queryRecordsByConstraint.return_value = []
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:data_files-detail', kwargs={'uid': 'NotThere.csv'})
        resp = self.client.get(url, {'version': 1})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_data_files_retrieve_502_html(self, _auth):
        from nextseek_api.services.data_files import DataFileProxyViewSet
        DataFileProxyViewSet.client.get_data_file = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:data_files-detail', kwargs={'uid': '560'})
        resp = self.client.get(url, {'version': 1})
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_data_files_create_422_invalid_body(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:data_files-list')
        bad_payload = {"data": {"type": "data_files", "attributes": {}, "relationships": {}}}
        resp = self.client.post(url, data=json.dumps(bad_payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_data_files_create_201(self, _auth):
        from nextseek_api.services.data_files import DataFileProxyViewSet
        DataFileProxyViewSet.client.create_data_file = Mock(return_value=(
            json.dumps(self._good_single_payload("561")).encode(), 201, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:data_files-list')
        payload = {
            "data": {
                "type": "data_files",
                "attributes": {"title": "DF-20240101-01_Sample-X.csv", "content_blobs": [{"url": "https://example.com/file.csv"}]},
                "relationships": {"projects": {"data": [{"id": "560", "type": "projects"}]}}
            }
        }
        resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertIn(resp.status_code, (status.HTTP_201_CREATED, status.HTTP_200_OK))

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_data_files_patch_422_mismatched_id(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:data_files-detail', kwargs={'uid': '560'})
        payload = {"data": {"type": "data_files", "id": "999", "attributes": {"description": "x"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        # Without upstream call mocked, this returns 422 from our service validation
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_data_files_patch_404_no_resolution(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:data_files-detail', kwargs={'uid': 'NotThere.csv'})
        payload = {"data": {"type": "data_files", "attributes": {"description": "x"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_data_files_patch_200(self, _auth):
        from nextseek_api.services.data_files import DataFileProxyViewSet
        DataFileProxyViewSet.client.update_data_file = Mock(return_value=(
            json.dumps(self._good_single_payload("560")).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:data_files-detail', kwargs={'uid': '560'})
        payload = {"data": {"type": "data_files", "id": "560", "attributes": {"description": "x"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=None)
    def test_data_files_patch_401(self, _auth):
        url = reverse('nextseek_api:data_files-detail', kwargs={'uid': '560'})
        payload = {"data": {"type": "data_files", "id": "560", "attributes": {"description": "x"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_data_files_patch_502_html(self, _auth):
        from nextseek_api.services.data_files import DataFileProxyViewSet
        DataFileProxyViewSet.client.update_data_file = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:data_files-detail', kwargs={'uid': '560'})
        payload = {"data": {"type": "data_files", "id": "560", "attributes": {"description": "x"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)


class ProjectProxyViewSetTests(APITestCase):
    """Minimal tests for Projects proxy endpoints covering 200/401/404/422/502 paths."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()

    def _good_index_payload(self):
        return {
            "data": [
                {
                    "id": "2558",
                    "type": "projects",
                    "attributes": {"title": "A Project"},
                    "links": {"self": "/projects/2558"}
                }
            ],
            "jsonapi": {"version": "1.0"},
            "links": {"self": "/projects?page[number]=1&page[size]=100"},
            "meta": {"base_url": "http://localhost:3000", "api_version": "v1"}
        }

    def _good_single_payload(self, pid="2558"):
        return {
            "data": {
                "id": pid,
                "type": "projects",
                "attributes": {"title": "A Project"},
                "relationships": {
                    "people": {"data": []},
                    "projects": {"data": []},
                    "institutions": {"data": []},
                    "investigations": {"data": []},
                    "studies": {"data": []},
                    "assays": {"data": []},
                    "data_files": {"data": []},
                    "documents": {"data": []},
                    "models": {"data": []},
                    "sops": {"data": []},
                    "publications": {"data": []},
                    "presentations": {"data": []},
                    "events": {"data": []},
                    "workflows": {"data": []},
                    "collections": {"data": []}
                },
                "links": {"self": f"/projects/{pid}"},
                "meta": {}
            },
            "jsonapi": {"version": "1.0"}
        }

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_projects_list_200(self, _auth):
        from nextseek_api.services.projects import ProjectProxyViewSet
        ProjectProxyViewSet.client.list_projects = Mock(return_value=(
            json.dumps(self._good_index_payload()).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:projects-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=None)
    def test_projects_list_401(self, _auth):
        url = reverse('nextseek_api:projects-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_projects_list_502_html(self, _auth):
        from nextseek_api.services.projects import ProjectProxyViewSet
        ProjectProxyViewSet.client.list_projects = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:projects-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)


class InvestigationProxyViewSetTests(APITestCase):
    """Tests for Investigations proxy covering 200/201/401/404/422/502 paths."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()

    def _good_index_payload(self):
        return {
            "data": [
                {
                    "id": "763",
                    "type": "investigations",
                    "attributes": {"title": "My Investigation"},
                    "links": {"self": "/investigations/763"}
                }
            ],
            "jsonapi": {"version": "1.0"},
            "links": {"self": "/investigations?page[number]=1&page[size]=100"},
            "meta": {"base_url": "http://localhost:3000", "api_version": "v1"}
        }

    def _good_single_payload(self, iid="763"):
        return {
            "data": {
                "id": iid,
                "type": "investigations",
                "attributes": {"title": "My Investigation"},
                "relationships": {"projects": {"data": []}},
                "links": {"self": f"/investigations/{iid}"},
                "meta": {}
            },
            "jsonapi": {"version": "1.0"}
        }

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_investigations_list_200(self, _auth):
        from nextseek_api.services.investigations import InvestigationProxyViewSet
        InvestigationProxyViewSet.client.list_investigations = Mock(return_value=(
            json.dumps(self._good_index_payload()).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:investigations-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=None)
    def test_investigations_list_401(self, _auth):
        url = reverse('nextseek_api:investigations-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_investigations_list_502_html(self, _auth):
        from nextseek_api.services.investigations import InvestigationProxyViewSet
        InvestigationProxyViewSet.client.list_investigations = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:investigations-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_investigations_retrieve_200_numeric_uid(self, _auth):
        from nextseek_api.services.investigations import InvestigationProxyViewSet
        InvestigationProxyViewSet.client.get_investigation = Mock(return_value=(
            json.dumps(self._good_single_payload("763")).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:investigations-detail', kwargs={'uid': '763'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_investigations_retrieve_404_unresolved_uid(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:investigations-detail', kwargs={'uid': 'INV-XYZ'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_investigations_retrieve_502_html(self, _auth):
        from nextseek_api.services.investigations import InvestigationProxyViewSet
        InvestigationProxyViewSet.client.get_investigation = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:investigations-detail', kwargs={'uid': '763'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_investigations_create_201(self, _auth):
        from nextseek_api.services.investigations import InvestigationProxyViewSet
        InvestigationProxyViewSet.client.create_investigation = Mock(return_value=(
            json.dumps(self._good_single_payload("764")).encode(), 201, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:investigations-list')
        payload = {
            "data": {
                "type": "investigations",
                "attributes": {"title": "My Investigation"},
                "relationships": {"projects": {"data": [{"type": "projects", "id": "4475"}]}}
            }
        }
        resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertIn(resp.status_code, (status.HTTP_201_CREATED, status.HTTP_200_OK))

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_investigations_create_422_invalid_body(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:investigations-list')
        bad_payload = {"data": {"type": "investigations", "attributes": {}, "relationships": {}}}
        resp = self.client.post(url, data=json.dumps(bad_payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_investigations_patch_200(self, _auth):
        from nextseek_api.services.investigations import InvestigationProxyViewSet
        InvestigationProxyViewSet.client.update_investigation = Mock(return_value=(
            json.dumps(self._good_single_payload("763")).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:investigations-detail', kwargs={'uid': '763'})
        payload = {"data": {"type": "investigations", "id": "763", "attributes": {"title": "Revised Title"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=None)
    def test_investigations_patch_401(self, _auth):
        url = reverse('nextseek_api:investigations-detail', kwargs={'uid': '763'})
        payload = {"data": {"type": "investigations", "id": "763", "attributes": {"title": "Revised Title"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_investigations_patch_404_no_resolution(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:investigations-detail', kwargs={'uid': 'INV-XYZ'})
        payload = {"data": {"type": "investigations", "attributes": {"title": "Revised Title"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_investigations_patch_502_html(self, _auth):
        from nextseek_api.services.investigations import InvestigationProxyViewSet
        InvestigationProxyViewSet.client.update_investigation = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:investigations-detail', kwargs={'uid': '763'})
        payload = {"data": {"type": "investigations", "id": "763", "attributes": {"title": "Revised Title"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)


class AssayProxyViewSetTests(APITestCase):
    """Tests for Assays proxy covering 200/201/401/404/422/502 paths."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()

    def _good_index_payload(self):
        return {
            "data": [
                {
                    "id": "351",
                    "type": "assays",
                    "attributes": {"title": "A Maximal experimental Assay"},
                    "links": {"self": "/assays/351"}
                }
            ],
            "jsonapi": {"version": "1.0"},
            "links": {"self": "/assays?page[number]=1&page[size]=100"},
            "meta": {"base_url": "http://localhost:3000", "api_version": "v1"}
        }

    def _good_single_payload(self, aid="351"):
        return {
            "data": {
                "id": aid,
                "type": "assays",
                "attributes": {"title": "A Maximal experimental Assay"},
                "relationships": {
                    "study": {"data": {"type": "studies", "id": "434"}},
                    "projects": {"data": []},
                    "sops": {"data": []},
                    "data_files": {"data": []}
                },
                "links": {"self": f"/assays/{aid}"},
                "meta": {}
            },
            "jsonapi": {"version": "1.0"}
        }

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_assays_list_200(self, _auth):
        from nextseek_api.services.assays import AssayProxyViewSet
        AssayProxyViewSet.client.list_assays = Mock(return_value=(
            json.dumps(self._good_index_payload()).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:assays-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=None)
    def test_assays_list_401(self, _auth):
        url = reverse('nextseek_api:assays-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_assays_list_502_html(self, _auth):
        from nextseek_api.services.assays import AssayProxyViewSet
        AssayProxyViewSet.client.list_assays = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:assays-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_assays_retrieve_200_numeric_uid(self, _auth):
        from nextseek_api.services.assays import AssayProxyViewSet
        AssayProxyViewSet.client.get_assay = Mock(return_value=(
            json.dumps(self._good_single_payload("351")).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:assays-detail', kwargs={'uid': '351'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_assays_retrieve_404_unresolved_uid(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:assays-detail', kwargs={'uid': 'ASSAY-XYZ'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_assays_retrieve_502_html(self, _auth):
        from nextseek_api.services.assays import AssayProxyViewSet
        AssayProxyViewSet.client.get_assay = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:assays-detail', kwargs={'uid': '351'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_assays_create_201(self, _auth):
        from nextseek_api.services.assays import AssayProxyViewSet
        AssayProxyViewSet.client.create_assay = Mock(return_value=(
            json.dumps(self._good_single_payload("352")).encode(), 201, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:assays-list')
        payload = {
            "data": {
                "type": "assays",
                "attributes": {
                    "title": "A Maximal experimental Assay",
                    "assay_class": {"key": "EXP"},
                    "assay_type": {"uri": "http://jermontology.org/ontology/JERMOntology#Transcriptomics"}
                },
                "relationships": {"study": {"data": {"type": "studies", "id": "434"}}}
            }
        }
        resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertIn(resp.status_code, (status.HTTP_201_CREATED, status.HTTP_200_OK))

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_assays_create_422_invalid_body(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:assays-list')
        bad_payload = {"data": {"type": "assays", "attributes": {}, "relationships": {}}}
        resp = self.client.post(url, data=json.dumps(bad_payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_assays_patch_200(self, _auth):
        from nextseek_api.services.assays import AssayProxyViewSet
        AssayProxyViewSet.client.update_assay = Mock(return_value=(
            json.dumps(self._good_single_payload("351")).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:assays-detail', kwargs={'uid': '351'})
        payload = {"data": {"type": "assays", "id": "351", "attributes": {"description": "Revised"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=None)
    def test_assays_patch_401(self, _auth):
        url = reverse('nextseek_api:assays-detail', kwargs={'uid': '351'})
        payload = {"data": {"type": "assays", "id": "351", "attributes": {"description": "Revised"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_assays_patch_404_no_resolution(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:assays-detail', kwargs={'uid': 'ASSAY-XYZ'})
        payload = {"data": {"type": "assays", "attributes": {"description": "Revised"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_assays_patch_502_html(self, _auth):
        from nextseek_api.services.assays import AssayProxyViewSet
        AssayProxyViewSet.client.update_assay = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:assays-detail', kwargs={'uid': '351'})
        payload = {"data": {"type": "assays", "id": "351", "attributes": {"description": "Revised"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

class PeopleProxyViewSetTests(APITestCase):
    """Tests for People proxy covering 200/201/401/404/422/502 paths."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()

    def _good_index_payload(self):
        return {
            "data": [
                {
                    "id": "1652",
                    "type": "people",
                    "attributes": {"title": "Doe, John"},
                    "links": {"self": "/people/1652"}
                }
            ],
            "jsonapi": {"version": "1.0"},
            "links": {"self": "/people?page[number]=1&page[size]=100"},
            "meta": {"base_url": "http://localhost:3000", "api_version": "v1"}
        }

    def _good_single_payload(self, pid="1652"):
        return {
            "data": {
                "id": pid,
                "type": "people",
                "attributes": {"title": "Doe, John", "mbox_sha1sum": "abc123"},
                "relationships": {
                    "projects": {"data": []},
                    "institutions": {"data": []},
                    "investigations": {"data": []},
                    "studies": {"data": []},
                    "assays": {"data": []},
                    "data_files": {"data": []},
                    "documents": {"data": []},
                    "models": {"data": []},
                    "sops": {"data": []},
                    "publications": {"data": []},
                    "events": {"data": []},
                    "presentations": {"data": []},
                    "collections": {"data": []},
                    "workflows": {"data": []}
                },
                "links": {"self": f"/people/{pid}"},
                "meta": {}
            },
            "jsonapi": {"version": "1.0"}
        }

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_people_list_200(self, _auth):
        from nextseek_api.services.people import PeopleProxyViewSet
        PeopleProxyViewSet.client.list_people = Mock(return_value=(
            json.dumps(self._good_index_payload()).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:people-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=None)
    def test_people_list_401(self, _auth):
        url = reverse('nextseek_api:people-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_people_list_502_html(self, _auth):
        from nextseek_api.services.people import PeopleProxyViewSet
        PeopleProxyViewSet.client.list_people = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:people-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_people_retrieve_200_numeric_uid(self, _auth):
        from nextseek_api.services.people import PeopleProxyViewSet
        PeopleProxyViewSet.client.get_person = Mock(return_value=(
            json.dumps(self._good_single_payload("1652")).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:people-detail', kwargs={'uid': '1652'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    @patch('nextseek_api.services.people.DBtable_people')
    def test_people_retrieve_404_string_uid_not_found(self, mock_dbp, _auth):
        mock_dbp.return_value.queryRecordsByConstraint.return_value = []
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:people-detail', kwargs={'uid': 'NotThere'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_people_retrieve_502_html(self, _auth):
        from nextseek_api.services.people import PeopleProxyViewSet
        PeopleProxyViewSet.client.get_person = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:people-detail', kwargs={'uid': '1652'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_people_create_201(self, _auth):
        from nextseek_api.services.people import PeopleProxyViewSet
        PeopleProxyViewSet.client.create_person = Mock(return_value=(
            json.dumps(self._good_single_payload("1700")).encode(), 201, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:people-list')
        payload = {"data": {"type": "people", "attributes": {"first_name": "Post", "last_name": "User", "email": "x@y"}}}
        resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertIn(resp.status_code, (status.HTTP_201_CREATED, status.HTTP_200_OK))

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_people_create_422_invalid_body(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:people-list')
        bad_payload = {"data": {"type": "people", "attributes": {}}}
        resp = self.client.post(url, data=json.dumps(bad_payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_people_patch_200(self, _auth):
        from nextseek_api.services.people import PeopleProxyViewSet
        PeopleProxyViewSet.client.update_person = Mock(return_value=(
            json.dumps(self._good_single_payload("1652")).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:people-detail', kwargs={'uid': '1652'})
        payload = {"data": {"type": "people", "id": "1652", "attributes": {"first_name": "Patched"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_people_patch_422_mismatched_id(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:people-detail', kwargs={'uid': '1652'})
        payload = {"data": {"type": "people", "id": "999", "attributes": {"first_name": "Patched"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_people_patch_404_no_resolution(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:people-detail', kwargs={'uid': 'NotThere'})
        payload = {"data": {"type": "people", "attributes": {"first_name": "Patched"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch('nextseek_api.helpers.get_auth', return_value=None)
    def test_people_patch_401(self, _auth):
        url = reverse('nextseek_api:people-detail', kwargs={'uid': '1652'})
        payload = {"data": {"type": "people", "id": "1652", "attributes": {"first_name": "Patched"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_people_patch_502_html(self, _auth):
        from nextseek_api.services.people import PeopleProxyViewSet
        PeopleProxyViewSet.client.update_person = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:people-detail', kwargs={'uid': '1652'})
        payload = {"data": {"type": "people", "id": "1652", "attributes": {"first_name": "Patched"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    @patch('nextseek_api.services.projects.DBtable_projects')
    def test_projects_retrieve_200_numeric_uid(self, mock_dbp, _auth):
        from nextseek_api.services.projects import ProjectProxyViewSet
        # numeric uid bypasses DB
        ProjectProxyViewSet.client.get_project = Mock(return_value=(
            json.dumps(self._good_single_payload("2558")).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:projects-detail', kwargs={'uid': '2558'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    @patch('nextseek_api.services.projects.DBtable_projects')
    def test_projects_retrieve_404_string_uid_not_found(self, mock_dbp, _auth):
        mock_dbp.return_value.queryRecordsByConstraint.return_value = []
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:projects-detail', kwargs={'uid': 'NotThere'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_projects_retrieve_502_html(self, _auth):
        from nextseek_api.services.projects import ProjectProxyViewSet
        ProjectProxyViewSet.client.get_project = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:projects-detail', kwargs={'uid': '2558'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_projects_create_422_invalid_body(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:projects-list')
        bad_payload = {"data": {"type": "projects", "attributes": {}, "relationships": {}}}
        resp = self.client.post(url, data=json.dumps(bad_payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_projects_create_201(self, _auth):
        from nextseek_api.services.projects import ProjectProxyViewSet
        ProjectProxyViewSet.client.create_project = Mock(return_value=(
            json.dumps(self._good_single_payload("2559")).encode(), 201, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:projects-list')
        payload = {"data": {"type": "projects", "attributes": {"title": "A Project"}}}
        resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertIn(resp.status_code, (status.HTTP_201_CREATED, status.HTTP_200_OK))

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_projects_patch_422_mismatched_id(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:projects-detail', kwargs={'uid': '2558'})
        payload = {"data": {"type": "projects", "id": "999", "attributes": {"title": "x"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_projects_patch_404_no_resolution(self, _auth):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:projects-detail', kwargs={'uid': 'NotThere'})
        payload = {"data": {"type": "projects", "attributes": {"title": "x"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_projects_patch_200(self, _auth):
        from nextseek_api.services.projects import ProjectProxyViewSet
        ProjectProxyViewSet.client.update_project = Mock(return_value=(
            json.dumps(self._good_single_payload("2558")).encode(), 200, {"Content-Type": "application/json"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:projects-detail', kwargs={'uid': '2558'})
        payload = {"data": {"type": "projects", "id": "2558", "attributes": {"title": "x"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('nextseek_api.helpers.get_auth', return_value=None)
    def test_projects_patch_401(self, _auth):
        url = reverse('nextseek_api:projects-detail', kwargs={'uid': '2558'})
        payload = {"data": {"type": "projects", "id": "2558", "attributes": {"title": "x"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('nextseek_api.helpers.get_auth', return_value=("u","p"))
    def test_projects_patch_502_html(self, _auth):
        from nextseek_api.services.projects import ProjectProxyViewSet
        ProjectProxyViewSet.client.update_project = Mock(return_value=(
            b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock()
        ))
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        url = reverse('nextseek_api:projects-detail', kwargs={'uid': '2558'})
        payload = {"data": {"type": "projects", "id": "2558", "attributes": {"title": "x"}}}
        resp = self.client.patch(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
