import json
from typing import Any

from pydantic import BaseModel, TypeAdapter


def schema_for(output_type: type[BaseModel]) -> dict[str, Any]:
    return TypeAdapter(output_type).json_schema()


def with_schema(output_type: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": output_type.__name__,
            "schema": schema_for(output_type),
            "strict": True,
        },
    }


def schema_instruction(output_type: type[BaseModel]) -> dict[str, str]:
    schema = json.dumps(schema_for(output_type), ensure_ascii=False)
    return {
        "role": "system",
        "content": "Respond with a single json object that conforms to this JSON Schema, "
        f"without any other text:\n{schema}",
    }


def decode(content: str, output_type: type[BaseModel]) -> BaseModel:
    # Разбор и проверка данных выполняются схемой, без исправления текста ответа.
    return TypeAdapter(output_type).validate_python(json.loads(content))
