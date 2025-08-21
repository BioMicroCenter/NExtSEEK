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
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)sertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
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
        self.assertEqual(len(response.data['results']), 25)parentIds', node_data)
    
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
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)ples-by-id-tree', kwargs={'pk': 123})
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



