from core import serializers
from core.tests.testcases import UnitTestCase


class MetadataSerializerTest(UnitTestCase):
    def test_update(self):
        with self.assertRaises(NotImplementedError):
            serializers.MetadataSerializer().update(None, None)

    def test_create(self):
        with self.assertRaises(NotImplementedError):
            serializers.MetadataSerializer().create(None)
