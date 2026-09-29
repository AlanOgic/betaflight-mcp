# ── Betaflight MCP Server ── Champs MSP décrits par table ───────────
# Lecture, validation et patch d'un payload MSP à partir d'une table de champs
# (offset, taille, plage firmware, énumération). Utilisé par pid_advanced et
# filter_config : une écriture ne modifie que les octets des champs demandés.

import struct
from dataclasses import dataclass
from typing import Optional, Union

Value = Union[int, str]


@dataclass(frozen=True)
class Field:
    offset:           int
    size:             int                    # octets par élément (1 ou 2, little-endian)
    minimum:          int
    maximum:          int
    signed:           bool = False
    enum:             Optional[tuple] = None # libellés CLI, index = valeur brute
    legacy_name:      Optional[str] = None   # nom CLI sur les firmwares plus anciens
    count:            int = 1                # > 1 : tableau, valeur CLI "a,b,c"
    legacy_u8_offset: Optional[int] = None   # copie u8 historique du même champ


def enum_field(offset: int, labels: tuple) -> Field:
    return Field(offset, 1, 0, len(labels) - 1, enum=labels)


def _format(field: Field) -> str:
    return {1: "b" if field.signed else "B", 2: "<h" if field.signed else "<H"}[field.size]


def is_present(field: Field, raw: bytes) -> bool:
    return field.offset + field.size * field.count <= len(raw)


def label(field: Field, value: int) -> Value:
    return field.enum[value] if field.enum and 0 <= value < len(field.enum) else value


def read(field: Field, raw: bytes) -> Value:
    values = [struct.unpack_from(_format(field), raw, field.offset + i * field.size)[0]
              for i in range(field.count)]
    if field.count > 1:
        return ",".join(str(v) for v in values)
    return label(field, values[0])


def describe(field: Field) -> str:
    if field.enum:
        return "valeurs acceptées : " + ", ".join(field.enum)
    bounds = f"[{field.minimum}–{field.maximum}]"
    if field.count > 1:
        return f"{field.count} valeurs 'a,b,c', chacune {bounds}"
    return f"plage firmware {bounds}"


def _check_int(name: str, field: Field, value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name}={value!r} : entier attendu ({describe(field)})")
    if not field.minimum <= value <= field.maximum:
        raise ValueError(f"{name}={value} hors {describe(field)}")
    return value


def _array_to_raw(name: str, field: Field, value) -> tuple[int, ...]:
    if not isinstance(value, str):
        raise ValueError(f"{name}={value!r} : format 'a,b,c' attendu ({describe(field)})")
    try:
        items = [int(part) for part in value.split(",")]
    except ValueError:
        raise ValueError(f"{name}={value!r} : format 'a,b,c' attendu ({describe(field)})") from None
    if len(items) != field.count:
        raise ValueError(f"{name}={value!r} : {field.count} valeurs attendues ({describe(field)})")
    return tuple(_check_int(name, field, item) for item in items)


def to_raw(fields: dict[str, Field], name: str, value) -> Union[int, tuple[int, ...]]:
    """Valeur brute d'un champ ; ValueError (message utilisateur) si invalide."""
    if name not in fields:
        raise ValueError(f"Champ '{name}' inconnu. Champs acceptés : {list(fields)}")
    field = fields[name]
    if field.count > 1:
        return _array_to_raw(name, field, value)
    if isinstance(value, str):
        if not field.enum or value.upper() not in field.enum:
            raise ValueError(f"{name}={value!r} invalide, {describe(field)}")
        return field.enum.index(value.upper())
    return _check_int(name, field, value)


def patch(fields: dict[str, Field], raw: bytes, raw_updates: dict) -> bytes:
    """Copie de raw avec les champs modifiés ; ValueError si un champ est absent du payload."""
    patched = bytearray(raw)
    for name, value in raw_updates.items():
        field  = fields[name]
        if not is_present(field, raw):
            raise ValueError(f"Champ {name} absent du payload firmware ({len(raw)} octets)")
        values = value if isinstance(value, tuple) else (value,)
        for i, item in enumerate(values):
            struct.pack_into(_format(field), patched, field.offset + i * field.size, item)
        if field.legacy_u8_offset is not None:
            patched[field.legacy_u8_offset] = values[0] & 0xFF
    return bytes(patched)
