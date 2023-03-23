from core.tests.testcases import UnitTestCase
from core.utils import ChoiceEnum


class TestEnum(ChoiceEnum):
    A = "choice a"
    B = "choice b"


class ChoiceEnumTest(UnitTestCase):
    def test_as_choices(self):
        self.assertEqual(TestEnum.as_choices(), [("A", "choice a"), ("B", "choice b")])
        self.assertEqual(TestEnum.as_choices(reverse=True), [("choice a", "A"), ("choice b", "B")])

    def test_as_dict(self):
        self.assertEqual(TestEnum.as_dict(), {"A": "choice a", "B": "choice b"})

    def test_names(self):
        self.assertEqual(TestEnum.names(), ["A", "B"])

    def test_lower_names(self):
        self.assertEqual(TestEnum.lower_names(), ["a", "b"])

    def test_values(self):
        self.assertEqual(TestEnum.values(), ["choice a", "choice b"])

    def test_value_from_name(self):
        self.assertEqual(TestEnum.value_from_name("A"), "choice a")
        self.assertEqual(TestEnum.value_from_name("B"), "choice b")
        self.assertEqual(TestEnum.value_from_name("C"), None)

    def test_from_name(self):
        self.assertEqual(TestEnum.from_name(TestEnum.A), TestEnum.A)
        self.assertEqual(TestEnum.from_name(TestEnum.B), TestEnum.B)
        self.assertEqual(TestEnum.from_name("A"), TestEnum.A)
        self.assertEqual(TestEnum.from_name("B"), TestEnum.B)
        self.assertEqual(TestEnum.from_name("C"), None)

    def test___str__(self):
        self.assertEqual(str(TestEnum.A), "A")
        self.assertEqual(str(TestEnum.B), "B")

    def test___repr__(self):
        self.assertEqual(TestEnum.A.__repr__(), "A")
        self.assertEqual(TestEnum.B.__repr__(), "B")

    def test___eq__(self):
        self.assertTrue(TestEnum.A == TestEnum.A)
        self.assertTrue(TestEnum.B == TestEnum.B)
        self.assertFalse(TestEnum.A == TestEnum.B)

    def test___hash__(self):
        self.assertEqual(hash(TestEnum.A), hash("A"))
        self.assertEqual(hash(TestEnum.B), hash("B"))
