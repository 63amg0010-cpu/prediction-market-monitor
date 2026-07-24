type YamlValue = (
    str | int | float | bool | None | list[YamlValue] | dict[str, YamlValue]
)

def load(stream: str) -> YamlValue: ...
