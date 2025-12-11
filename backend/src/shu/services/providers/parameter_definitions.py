"""Typed parameter definitions for provider parameter mapping (dataclass-based).

Adapters return these objects; serialization is handled via to_dict().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


def _serialize(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return value


@dataclass
class InputField:
    path: str
    type: str = "string"
    label: Optional[str] = None
    required: bool = False

    def to_dict(self) -> Dict[str, Any]:
        data = {"path": self.path, "type": self.type}
        if self.label is not None:
            data["label"] = self.label
        if self.required:
            data["required"] = True
        return data


@dataclass
class Option:
    value: Any
    label: Optional[str] = None
    help: Optional[str] = None
    input_fields: List[InputField] = field(default_factory=list)
    input_schema: Optional["ParameterBase"] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"value": _serialize(self.value)}
        if self.label is not None:
            data["label"] = self.label
        if self.help is not None:
            data["help"] = self.help
        if self.input_fields:
            data["input_fields"] = [_serialize(f) for f in self.input_fields]
        if self.input_schema is not None:
            data["input_schema"] = _serialize(self.input_schema)
        return data


@dataclass
class ParameterBase:
    type: str
    label: Optional[str] = None
    description: Optional[str] = None
    placeholder: Optional[str] = None
    default: Any = None
    options: List[Option] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"type": self.type}
        if self.label is not None:
            data["label"] = self.label
        if self.description is not None:
            data["description"] = self.description
        if self.placeholder is not None:
            data["placeholder"] = self.placeholder
        if self.default is not None:
            data["default"] = _serialize(self.default)
        if self.options:
            data["options"] = [_serialize(o) for o in self.options]
        return data


@dataclass
class NumberParameter(ParameterBase):
    min: Optional[float] = None
    max: Optional[float] = None
    type: str = field(init=False, default="number")

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        if self.min is not None:
            data["min"] = self.min
        if self.max is not None:
            data["max"] = self.max
        return data


@dataclass
class IntegerParameter(NumberParameter):
    type: str = field(init=False, default="integer")


@dataclass
class StringParameter(ParameterBase):
    type: str = field(init=False, default="string")


@dataclass
class BooleanParameter(ParameterBase):
    type: str = field(init=False, default="boolean")
    options: List[Option] = field(default_factory=list)


@dataclass
class EnumParameter(ParameterBase):
    type: str = field(init=False, default="enum")


@dataclass
class ArrayParameter(ParameterBase):
    items: Optional[ParameterBase] = None
    type: str = field(init=False, default="array")

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        if self.items is not None:
            data["items"] = _serialize(self.items)
        return data


@dataclass
class ObjectParameter(ParameterBase):
    properties: Mapping[str, ParameterBase] = field(default_factory=dict)
    required: List[str] = field(default_factory=list)
    type: str = field(init=False, default="object")

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        if self.properties:
            data["properties"] = {k: _serialize(v) for k, v in self.properties.items()}
        if self.required:
            data["required"] = list(self.required)
        return data


def serialize_parameter_mapping(mapping: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: _serialize(v) for k, v in (mapping or {}).items()}
