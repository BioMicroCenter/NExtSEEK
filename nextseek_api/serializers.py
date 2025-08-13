from rest_framework import serializers
from seek.models import Samples, Data_files
from seek.dbtable_data_files import DBtable_data_files
import json

class SamplesSerializer1(serializers.ModelSerializer):
    title = serializers.CharField()
    sample_type_id = serializers.IntegerField()
    #json_metadata = serializers.TextField()
    uuid = serializers.CharField()
    contributor_id = serializers.IntegerField()
    policy_id = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    first_letter = serializers.CharField()
    #other_creators = serializers.TextField()
    originating_data_file_id = serializers.IntegerField()
    deleted_contributor = serializers.CharField()
    
    class Meta:
        model = Samples
        fields = ('__all__')

class SamplesSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Samples
        fields = ('id', 'title', 'json_metadata')

    def to_representation(self, instance):
        json_metadata = instance.json_metadata
        representation = json.loads(json_metadata)
        representation['id'] = instance.id
        
        dbdf = DBtable_data_files("DEFAULT")
        representation = dbdf.retrieveFileLinks(representation)
        return representation

class DatafilesSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Data_files
        fields = ('id', 'title')
        
    def __getFileLink(self, datafileUID):
        dbdf = DBtable_data_files("DEFAULT")
        msg, status, link = dbdf.downloadDF_fromStorage(None, uid)
        link = 'okay'
        return link
        
    def to_representation(self, instance):
        title = instance.title
        datafileUID = title
        dbdf = DBtable_data_files("DEFAULT")
        msg, status, link = dbdf.downloadDF_fromStorage(None, datafileUID)
        
        representation = {}
        representation['id'] = instance.id
        representation['title'] = title
        representation['link'] = link
        representation['msg'] = msg
        representation['status'] = status
        return representation

# Add these to the end of nextseek_api/serializers.py
class SampleNodeSerializer(serializers.Serializer):
    """Serializer for individual sample tree nodes."""
    id = serializers.CharField()
    uuid = serializers.CharField()
    type = serializers.CharField()
    color = serializers.CharField()
    parentIds = serializers.ListField(child=serializers.CharField())

class SampleTreeSerializer(serializers.ListSerializer):
    """Serializer for sample tree data (array of nodes)."""
    child = SampleNodeSerializer()
    
    class Meta:
        # This ensures proper OpenAPI documentation for array responses
        list_serializer_class = serializers.ListSerializer

