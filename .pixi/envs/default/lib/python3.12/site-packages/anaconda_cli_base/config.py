import os
import sys

from functools import cached_property
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple
from typing import Type
from typing import Union

from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings import PydanticBaseSettingsSource
from pydantic_settings import PyprojectTomlConfigSettingsSource
from pydantic_settings import SettingsConfigDict

from .exceptions import AnacondaConfigTomlSyntaxError, AnacondaConfigValidationError

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def anaconda_config_path() -> Path:
    return Path(
        os.path.expandvars(
            os.path.expanduser(
                os.getenv("ANACONDA_CONFIG_TOML", "~/.anaconda/config.toml")
            )
        )
    )


class AnacondaConfigTomlSettingsSource(PyprojectTomlConfigSettingsSource):
    def _read_file(self, file_path: Path) -> Dict[str, Any]:
        try:
            return super()._read_file(file_path)
        except tomllib.TOMLDecodeError as e:
            arg = f"{anaconda_config_path()}: {e.args[0]}"
            raise AnacondaConfigTomlSyntaxError(arg)

class AnacondaBaseSettings(BaseSettings):

    def __init_subclass__(
        cls, plugin_name: Optional[Union[str, tuple]] = None, **kwargs: Any
    ) -> None:
        base_env_prefix: str = "ANACONDA_"
        pyproject_toml_table_header: Tuple[str, ...]

        if plugin_name is None:
            pyproject_toml_table_header = ()
            env_prefix = base_env_prefix
        if isinstance(plugin_name, tuple):
            if not all(isinstance(entry, str) for entry in plugin_name):
                raise ValueError(f"plugin_name={plugin_name} error: All values must be strings.")
            pyproject_toml_table_header = ("plugin", *plugin_name)
            env_prefix = base_env_prefix + "_".join(plugin_name).upper() + "_"
        elif isinstance(plugin_name, str):
            pyproject_toml_table_header = ("plugin", plugin_name)
            env_prefix = base_env_prefix + f"{plugin_name.upper()}_"
        else:
            raise ValueError(f"plugin_name={plugin_name} is not supported. It must be either a str or tuple.")

        cls.model_config = SettingsConfigDict(
            env_file=".env",
            pyproject_toml_table_header=pyproject_toml_table_header,
            env_prefix=env_prefix,
            env_nested_delimiter="__",
            extra="ignore",
            ignored_types=(cached_property,),
        )

        return super().__init_subclass__(**kwargs)

    def __init__(self, **kwargs: Any) -> None:
        try:
            super().__init__(**kwargs)
        except ValidationError as e:
            errors = []
            for error in e.errors():
                input_value = error["input"]
                msg = error["msg"]

                env_prefix = self.model_config.get("env_prefix", "")
                delimiter = self.model_config.get("env_nested_delimiter", "") or ""
                env_var = env_prefix + delimiter.join(str(l).upper() for l in error["loc"])

                kwarg = error["loc"][0]
                if kwarg in kwargs:
                    value = kwargs[str(kwarg)]
                    msg = f"- Error in init kwarg {e.title}({error['loc'][0]}={value})\n    {msg}"
                elif env_var in os.environ:
                    msg = f"- Error in environment variable {env_var}={input_value}\n    {msg}"
                else:
                    table_header = ".".join(self.model_config.get("pyproject_toml_table_header", []))
                    key = ".".join(str(l) for l in error["loc"])
                    msg = f"- Error in {anaconda_config_path()} in [{table_header}] for {key} = {input_value}\n    {msg}"

                errors.append(msg)

            message = "\n" + "\n".join(errors)

            raise AnacondaConfigValidationError(message)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            AnacondaConfigTomlSettingsSource(settings_cls, anaconda_config_path()),
        )
