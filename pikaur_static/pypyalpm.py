# pylint: disable=invalid-name,line-too-long
"""
Pure-python alpm implementation backported from Pikaur v0.6
with compatibility layer added for easier integration with pyalpm interface.
"""
import gzip
import os
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from pprint import pformat
from typing import IO, Final, cast

NOT_FOUND_ATOM = object()


DB_NAME_LOCAL: Final = "local"


SUPPORTED_ALPM_VERSION: Final = "9"
# SUPPORTED_ALPM_VERSION: Final = "99999"  # used for testing only
PACMAN_EXECUTABLE = "pacman"
PACMAN_ROOT = "/var/lib/pacman"


class DB:
    name: str

    def search(self, query: str) -> list["Package"]:
        pkgs: dict[str, LocalPackageInfo] | dict[str, RepoPackageInfo]
        if self.name == DB_NAME_LOCAL:
            pkgs = PackageDB.get_local_dict()
        else:
            pkgs = PackageDB.get_repo_dict()
        if not query:
            return list(pkgs.values())
        return [
            pkg for pkg_name, pkg in pkgs.items()
            if query in pkg_name
        ]

    def get_pkg(self, name: str) -> "Package":
        if self.name == DB_NAME_LOCAL:
            return PackageDB.get_local_dict()[name]
        return PackageDB.get_repo_dict()[name]

    def __init__(self, name: str) -> None:
        self.name = name


class Package:
    db: DB
    # description properties
    name: str
    version: str
    desc: str
    url: str
    arch: str
    licenses: list[str]
    groups: list[str]
    # package properties
    packager: str
    md5sum: str
    sha256sum: str
    base64_sig: str
    filename: str
    base: str
    size: int
    isize: int
    reason: int
    builddate: int
    installdate: int
    # { "files",  (getter)pyalpm_package_get_files, 0, "list of installed files", NULL } ,
    # { "backup", (getter)_get_list_attribute, 0, "list of tuples (filename, md5sum)", &get_backup } ,  # noqa: E501,RUF100
    # { "deltas", (getter)_get_list_attribute, 0, "list of available deltas", &get_deltas } ,
    # /* dependency information */
    depends: list[str]
    optdepends: list[str]
    conflicts: list[str]
    provides: list[str]
    replaces: list[str]
    validation: str | None = None

    # /* miscellaneous information */
    # { "has_scriptlet", (getter)pyalpm_pkg_has_scriptlet, 0, "True if the package has an install script", NULL },  # noqa: E501,RUF100
    # { "download_size", (getter)pyalpm_pkg_download_size, 0, "predicted download size for this package", NULL },  # noqa: E501,RUF100

    def compute_requiredby(self) -> list[str]:
        return [
            pkg.name
            for pkg in PackageDB.get_local_list()
            if self.name in pkg.depends
        ]

    def compute_optionalfor(self) -> list[str]:
        return [
            pkg.name
            for pkg in PackageDB.get_local_list()
            if self.name in pkg.optdepends
        ]


################################################################################

PACMAN_LIST_FIELDS = (
    "conflicts",
    "replaces",
    "depends",
    "provides",
    "Required_By",
    "Optional_For",
    "licenses",
    "groups",
)


PACMAN_DICT_FIELDS = (
    "optdepends",
)


DB_INFO_TRANSLATION = {
    "%NAME%": "name",
    "%VERSION%": "version",
    "%PROVIDES%": "provides",
    "%DESC%": "desc",
    "%CONFLICTS%": "conflicts",
    "%DEPENDS%": "depends",
    "%OPTDEPENDS%": "optdepends",
    "%REPLACES%": "replaces",
    "%LICENSE%": "licenses",
    "%REASON%": "reason",
    "%SIZE%": "size",
    "%BUILDDATE%": "builddate",
    "%INSTALLDATE%": "installdate",
    "%PACKAGER%": "packager",
    "%URL%": "url",
    "%BASE%": "base",
    "%GROUPS%": "groups",
    "%ARCH%": "arch",
    "%VALIDATION%": "validation",
    "%XDATA%": "data",
}


# class PacmanPackageInfo(DataType):
# class PacmanPackageInfo:
class PacmanPackageInfo(Package):
    # name: str | None = None
    # base = None
    # version = None
    # desc = None
    # arch = None
    # url = None
    # licenses = None
    # groups = None
    # provides: Iterable[str] | None = None
    # depends = None
    # optdepends = None
    # conflicts = None
    # replaces = None
    # size = None
    # packager = None
    # builddate = None

    # db = None

    # validation = None
    data = None

    def __repr__(self) -> str:
        return f'<{self.__class__.__name__} "{self.name}">'

    @property
    def all(self) -> str:
        return pformat(self.__dict__)

    @classmethod
    def _parse_pacman_db_info(  # pylint: disable=too-many-branches,too-many-statements  # noqa: C901,E501,RUF100
            cls, db_file_name: str, open_method: Callable[[str], IO[bytes]],
    ) -> "Iterable[PacmanPackageInfo]":
        # print(f"{db_file_name=}")

        def verbose_setattr(
                pkg: "PacmanPackageInfo",
                real_field: str,
                value: str | list[str] | dict[str, str] | None,
        ) -> None:
            setattr(pkg, real_field, value)

        with open_method(db_file_name) as db_file:
            pkg = cls()
            value: str | list[str] | dict[str, str] | None
            line = field = real_field = value = None

            def return_pkg(pkg: PacmanPackageInfo) -> PacmanPackageInfo:
                for field in DB_INFO_TRANSLATION.values():
                    if getattr(pkg, field, NOT_FOUND_ATOM) is NOT_FOUND_ATOM:
                        if field in PACMAN_LIST_FIELDS:
                            setattr(pkg, field, [])
                        elif field in PACMAN_DICT_FIELDS:
                            setattr(pkg, field, {})
                        else:
                            setattr(pkg, field, None)

                return pkg

            while line != "":  # noqa: PLC1901
                line = db_file.readline().decode("utf-8")
                # print(line)
                if line.startswith("\x00\x00\x00"):
                    continue
                if line.startswith("%"):

                    if real_field and (field in DB_INFO_TRANSLATION):
                        verbose_setattr(pkg, real_field, value)

                    field = line.strip()
                    real_field = DB_INFO_TRANSLATION.get(field)
                    if not real_field:
                        continue

                    if real_field == "name" and getattr(pkg, "name", None):
                        yield return_pkg(pkg)
                        pkg = cls()

                    if real_field in PACMAN_LIST_FIELDS:
                        value = []
                    elif real_field in PACMAN_DICT_FIELDS:
                        value = {}
                    else:
                        value = ""
                else:
                    if field not in DB_INFO_TRANSLATION:
                        continue

                    _value = line.strip()
                    if not _value:
                        continue
                    if real_field in PACMAN_LIST_FIELDS:
                        cast(list[str], value).append(_value)
                    elif real_field in PACMAN_DICT_FIELDS:
                        subkey, *subvalue_parts = _value.split(": ")
                        subvalue = ": ".join(subvalue_parts)
                        # pylint: disable=unsupported-assignment-operation
                        cast(dict[str, str], value)[subkey] = subvalue
                    else:
                        value = cast(str, value) + _value

            if field in DB_INFO_TRANSLATION:
                if not real_field:
                    raise RuntimeError(field)
                # if real_field in {"validation", "data"}:
                    # print(f"{real_field=} {value=}")
                verbose_setattr(pkg, real_field, value)

            yield return_pkg(pkg)

    @classmethod
    def parse_pacman_db_gzip_info(cls, file_name: str) -> "Iterable[PacmanPackageInfo]":
        return cls._parse_pacman_db_info(file_name, gzip.open)  # type: ignore[arg-type]

    @classmethod
    def parse_pacman_db_info(cls, file_name: str) -> "Iterable[PacmanPackageInfo]":
        return cls._parse_pacman_db_info(
            file_name, lambda x: open(x, "rb"),  # pylint: disable=consider-using-with  # noqa: SIM115,E501,RUF100,PTH123
        )


def get_package_name_from_depend_line(depend_line: str) -> str:
    return depend_line.split("=")[0].split("<")[0].split(">")[0]


class RepoPackageInfo(PacmanPackageInfo):
    pass
    # Repository = None
    # Download_Size = None


class LocalPackageInfo(PacmanPackageInfo):
    # Required_By = None
    # Optional_For = None
    # installdate = None
    # reason = None
    # Install_Script = None
    pass


class PackageDBCommon:

    _repo_cache: list[RepoPackageInfo] | None = None
    _local_cache: list[LocalPackageInfo] | None = None
    _repo_dict_cache: dict[str, RepoPackageInfo] | None = None
    _local_dict_cache: dict[str, LocalPackageInfo] | None = None
    _repo_provided_cache: list[str] | None = None
    _local_provided_cache: list[str] | None = None

    repo = "repo"
    local = "local"

    @classmethod
    def get_repo_list(cls) -> list[RepoPackageInfo]:
        if not cls._repo_cache:
            cls._repo_cache = list(cls.get_repo_dict().values())
        return cls._repo_cache

    @classmethod
    def get_local_list(cls) -> list[LocalPackageInfo]:
        if not cls._local_cache:
            cls._local_cache = list(cls.get_local_dict().values())
        return cls._local_cache

    @classmethod
    def get_repo_dict(cls) -> dict[str, RepoPackageInfo]:
        if not cls._repo_dict_cache:
            cls._repo_dict_cache = {
                pkg.name: pkg
                for pkg in cls.get_repo_list()  # pylint: disable=not-an-iterable
            }
        return cls._repo_dict_cache

    @classmethod
    def get_local_dict(cls) -> dict[str, LocalPackageInfo]:
        if not cls._local_dict_cache:
            cls._local_dict_cache = {
                pkg.name: pkg
                for pkg in cls.get_local_list()  # pylint: disable=not-an-iterable
            }
        return cls._local_dict_cache

    @classmethod
    def _get_provided(cls, local: str) -> list[str]:
        pkgs: list[LocalPackageInfo] | list[RepoPackageInfo] = (
            cls.get_local_list()
            if local == cls.local
            else cls.get_repo_list()
        )
        return [
            get_package_name_from_depend_line(provided_pkg)
            for pkg in pkgs  # pylint: disable=not-an-iterable
            for provided_pkg in pkg.provides
            if pkg.provides
        ]

    @classmethod
    def get_repo_provided(cls) -> list[str]:
        if not cls._repo_provided_cache:
            cls._repo_provided_cache = cls._get_provided(cls.repo)
        return cls._repo_provided_cache

    @classmethod
    def get_local_provided(cls) -> list[str]:
        if not cls._local_provided_cache:
            cls._local_provided_cache = cls._get_provided(cls.local)
        return cls._local_provided_cache


# class DB(DataType):
# class DB:
#     name: str

#     def __init__(self, name: str) -> None:
#         self.name = name


class PackageDB_ALPM9(PackageDBCommon):  # pylint: disable=invalid-name  # noqa: N801

    # ~2.7 seconds (was ~2.2 seconds with gzip)

    _repo_db_names: list[str] | None = None
    sync_dir = f"{PACMAN_ROOT}/sync/"

    @classmethod
    def get_db_names(cls) -> list[str]:
        if not cls._repo_db_names:
            cls._repo_db_names = [
                repo_name.rsplit(".db", maxsplit=1)[0]
                for repo_name in os.listdir(cls.sync_dir)
                if repo_name and repo_name.endswith(".db")
            ]
        return cls._repo_db_names

    @classmethod
    def get_repo_dict_pygzip(cls) -> dict[str, RepoPackageInfo]:
        if not cls._repo_dict_cache:
            # print("REPO_NOT_CACHED")

            result = {}
            for repo_name in os.listdir(cls.sync_dir):
                if not repo_name.endswith(".db"):
                    continue

                repo_path = os.path.join(cls.sync_dir, repo_name)
                for pkg in RepoPackageInfo.parse_pacman_db_gzip_info(repo_path):
                    pkg.db = DB(name=repo_name.rsplit(".db", maxsplit=1)[0])
                    result[pkg.name] = cast(RepoPackageInfo, pkg)

            cls._repo_dict_cache = result
            # print("REPO_DONE")
        return cls._repo_dict_cache

    @classmethod
    def get_repo_dict(cls) -> dict[str, RepoPackageInfo]:
        # Qu repo 2.6 s, Qu 3.6 s:
        return cls.get_repo_dict_pygzip()

    @classmethod
    def get_local_dict(cls) -> dict[str, LocalPackageInfo]:
        if not cls._local_dict_cache:
            # print("LOCAL_NOT_CACHED")
            result: dict[str, LocalPackageInfo] = {}
            local_dir = f"{PACMAN_ROOT}/local/"
            for pkg_dir_name in os.listdir(local_dir):
                if not os.path.isdir(os.path.join(local_dir, pkg_dir_name)):
                    continue
                for pkg in LocalPackageInfo.parse_pacman_db_info(
                        os.path.join(local_dir, pkg_dir_name, "desc"),
                ):
                    # print(pkg_dir_name, dir(pkg))
                    result[pkg.name] = cast(LocalPackageInfo, pkg)
            cls._local_dict_cache = result
            # print("LOCAL_DONE")
        # print(cls._local_dict_cache)
        return cls._local_dict_cache


with Path(f"{PACMAN_ROOT}/local/ALPM_DB_VERSION").open(encoding="utf-8") as version_file:
    ALPM_DB_VER = version_file.read().strip()
    PackageDB: type[PackageDBCommon]
    # VANILLA pikaur + cpython + pyalpm: -Qu --repo: ~ 1.2..1.4 s
    if ALPM_DB_VER == SUPPORTED_ALPM_VERSION:
        # CPYTHON: -Qu --repo: ~ 2.6..3.1 s
        # NUITKA: -Qu --repo: ~ 3.2..3.7 s
        # NUITKA_static: -Qu --repo: ~ 3.1..3.9 s
        PackageDB = PackageDB_ALPM9
    else:
        # CPYTHON: -Qu --repo: ~ 2.8..3.3 s
        # raise RuntimeError(ALPM_DB_VER)
        from pacman_fallback import get_pacman_cli_package_db
        PackageDB = get_pacman_cli_package_db(
            PackageDBCommon=PackageDBCommon,
            RepoPackageInfo=RepoPackageInfo,
            LocalPackageInfo=LocalPackageInfo,
            PACMAN_DICT_FIELDS=PACMAN_DICT_FIELDS,
            PACMAN_LIST_FIELDS=PACMAN_LIST_FIELDS,
            PACMAN_EXECUTABLE=PACMAN_EXECUTABLE,
        )


################################################################################


class LooseVersion:

    component_re = re.compile(r"(\d+ | r | [a-z0-9]+ | \.)", re.VERBOSE)

    def __init__(self, vstring: str | None = None) -> None:
        if vstring:
            self.parse(vstring)

    def __eq__(self, other: "LooseVersion | str") -> bool:  # type: ignore[override]
        c = self._cmp(other)
        if c is NotImplemented:
            return NotImplemented
        return c == 0

    def __lt__(self, other: "LooseVersion | str") -> bool:
        c = self._cmp(other)
        if c is NotImplemented:
            return NotImplemented
        return c < 0

    def __le__(self, other: "LooseVersion | str") -> bool:
        c = self._cmp(other)
        if c is NotImplemented:
            return NotImplemented
        return c <= 0

    def __gt__(self, other: "LooseVersion | str") -> bool:
        c = self._cmp(other)
        if c is NotImplemented:
            return NotImplemented
        return c > 0

    def __ge__(self, other: "LooseVersion | str") -> bool:
        c = self._cmp(other)
        if c is NotImplemented:
            return NotImplemented
        return c >= 0

    def parse(self, vstring: str) -> None:
        self.vstring = vstring
        components = [x for x in self.component_re.split(vstring) if x and x != "."]
        self.version = components

    def __str__(self) -> str:
        return self.vstring

    def __hash__(self) -> int:
        return hash(self.vstring)

    def __repr__(self) -> str:
        return f"LooseVersion ('{self}')"

    def _cmp(self, other: "str | LooseVersion") -> int:
        other = self._coerce(other)
        if other is NotImplemented:
            return NotImplemented

        components = self.version
        components_other = other.version
        components_len = len(components)
        components_other_len = len(components_other)
        max_len = max(components_len, components_other_len)
        for i in range(max_len):
            error_counter = 0
            component_parsed: int | str
            component_other_parsed: int | str
            try:
                component_parsed = int(components[i]) if i < components_len else 0
            except ValueError:
                component_parsed = 0
                error_counter += 1
            try:
                component_other_parsed = int(components_other[i]) if i < components_other_len else 0
            except ValueError:
                component_other_parsed = 0
                error_counter += 1
            if error_counter == 2:  # noqa: PLR2004
                component_parsed = components[i]
                component_other_parsed = components_other[i]
            if i < components_len:
                components[i] = component_parsed
            else:
                components.append(component_parsed)
            if i < components_other_len:
                components_other[i] = component_other_parsed
            else:
                components_other.append(component_other_parsed)

        if components == components_other:
            return 0
        if components < components_other:
            # print(f"-1 {components=} {components_other=}")
            return -1
        if components > components_other:
            # print(f"1 {components=} {components_other=}")
            return 1
        return NotImplemented

    def cmp(self, other: "str | LooseVersion") -> int:
        return self._cmp(other)

    @classmethod
    def _coerce(cls, other: "str | LooseVersion") -> "LooseVersion":
        if isinstance(other, cls):
            return other
        if isinstance(other, str):
            return cls(other)
        return NotImplemented


def compare_versions(current_version: str, new_version: str) -> int:
    for separator in ("+", ):
        current_version = current_version.replace(separator, ".")
        new_version = new_version.replace(separator, ".")
    current_base_version = new_base_version = None
    for separator in (":", "-"):
        if separator in current_version:
            current_base_version, _current_version = \
                current_version.split(separator)[:2]
        if separator in new_version:
            new_base_version, _new_version = \
                new_version.split(separator)[:2]
        if (
                current_base_version and new_base_version
        ) and (
            current_base_version != new_base_version
        ):
            current_version = current_base_version
            new_version = new_base_version
            break

    return LooseVersion(current_version).cmp(new_version)


################################################################################
