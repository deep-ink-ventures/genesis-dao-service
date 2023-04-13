from enum import Enum
from typing import Optional, Union

from drf_extra_fields.fields import Base64ImageField


class ChoiceEnum(Enum):
    @classmethod
    def as_choices(cls, reverse=False):
        return [(tag.value, tag.name) if reverse else (tag.name, tag.value) for tag in cls]

    @classmethod
    def as_dict(cls):
        return {tag.name: tag.value for tag in cls}

    @classmethod
    def names(cls):
        return [tag.name for tag in cls]

    @classmethod
    def lower_names(cls):
        return [tag.name.lower() for tag in cls]

    @classmethod
    def values(cls):
        return [tag.value for tag in cls]

    @classmethod
    def value_from_name(cls, name: str) -> Optional[str]:
        for tag in cls:
            if tag.name == name:
                return tag.value

    @classmethod
    def from_name(cls, name: Union[str, "ChoiceEnum"]) -> Optional["ChoiceEnum"]:
        if isinstance(name, cls):
            return name
        try:
            return getattr(cls, name)
        except Exception:  # noqa E722
            return None

    def __str__(self):
        return self.name

    def __repr__(self):
        return self.name

    def __eq__(self, other):
        return other == self.name

    def __hash__(self):
        return hash(self.name)


class B64ImageField(Base64ImageField):
    ALLOWED_TYPES = Base64ImageField.ALLOWED_TYPES
    INVALID_FILE_MESSAGE = f"Invalid image file. Allowed image types are: {', '.join(Base64ImageField.ALLOWED_TYPES)}."
