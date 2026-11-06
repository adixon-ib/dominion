"""TicketTest: a ticket test as a class.

    class Oplog(TicketTest):
        name = "NIOS-1"
        home_state = "Licenses"
        deploy_files = {"products/one/x.py": "/infoblox/one/lib/python/x.py"}

        def probe(self): return self.app.usage_mode()
        def home(self): reset_all(self.app)

        @step("Licenses", "IPc", "enable")
        def enable(self):
            with self.checks("enable") as c: ...

    Oplog(HOST, worktree=WORKTREE, results_dir=DIR).run()

run() drives Ticket and Ticket.steps: phases `1. connect`, `2. deploy`,
`3. preflight`, then one state step per @step method, numbered from 4, then
home. Steps run in class definition order, inherited steps first. A subclass
that redefines an inherited step name keeps the inherited position.
"""

from __future__ import annotations

import abc
import os
from typing import ClassVar, NoReturn

from .constants import WAPI_VERSION
from .ticket import Checks, Ticket

FIRST_STEP_NUMBER = 4  # after connect, deploy, preflight


def step(src, dst, title: str):
    """Mark a method as a state step from src to dst, shown as `<n>. <title>`."""
    def mark(fn):
        fn._step = (src, dst, title)
        return fn
    return mark


class TicketTest(abc.ABC):
    name: str  # Ticket name, also the report title
    home_state: object  # the state home() must reach
    deploy_files: ClassVar[dict[str, str]] = {}  # worktree subpath -> container path
    deploy_groups: ClassVar[set[str]] = set()  # deploy_token_py groups, empty = none
    initial_state: ClassVar[dict] = {}  # Ticket(state=...), becomes self.state

    def __init__(  # noqa: PLR0913
        self,
        host: str | None,
        *,
        worktree: str | None = None,
        results_dir: str | None = None,
        wapi_version: str = WAPI_VERSION,
        skip_deploy: bool = False,
        skip_home: bool = False,
    ):
        for attr in ("name", "home_state"):
            if not hasattr(type(self), attr):
                raise TypeError(f"{type(self).__name__} must define {attr}")
        self.host = host
        self.worktree = worktree
        self.results_dir = results_dir
        self.wapi_version = wapi_version
        self.skip_deploy = skip_deploy
        self.skip_home = skip_home
        self._t: Ticket | None = None

    @abc.abstractmethod
    def probe(self) -> object:
        """The box's current state."""

    @abc.abstractmethod
    def home(self) -> None:
        """Bring the box to home_state."""

    def preflight(self) -> None:
        """Default: the box is in home_state. Override for more and call super()."""
        with self.checks("preflight") as c:
            c.expect(f"box in {self.home_state!r}", self.home_state, self.probe())

    def deploy(self) -> None:
        """Upload deploy_files (md5-checked), deploy deploy_groups, wait for WAPI.
        A file absent from the worktree is noted and not uploaded."""
        for sub, dest in self.deploy_files.items():
            if not self.worktree:
                raise RuntimeError(f"no worktree given to upload {sub}")
            path = os.path.join(self.worktree, sub)
            if os.path.isfile(path):
                self.container.upload_file(path, dest)
            else:
                self.t.note(f"{sub} absent in worktree: not uploaded")
        if self.deploy_groups:
            result = self.app.deploy_token_py(groups=self.deploy_groups)
            skipped = result.details.get("skipped")
            if skipped:
                self.t.note(f"deploy_token_py skipped (absent in worktree): {skipped}")
        self.app.wapi.wait_until_ready()

    @property
    def t(self) -> Ticket:
        if self._t is None:
            raise RuntimeError("not running: call run()")
        return self._t

    @property
    def app(self):
        return self.t.app

    @property
    def container(self):
        return self.t.container

    @property
    def state(self):
        return self.t.state

    def checks(self, what: str) -> Checks:
        return self.t.checks(what)

    def nios_log(self, pattern: str):
        """Ticket.nios_log: a context manager yielding a NiosLog."""
        return self.t.nios_log(pattern)

    @classmethod
    def _steps(cls) -> list[tuple[str, tuple]]:
        found: dict[str, tuple] = {}
        for klass in reversed(cls.__mro__):
            for attr, fn in vars(klass).items():
                meta = getattr(fn, "_step", None)
                if meta is not None:
                    found[attr] = meta
        return list(found.items())

    def _deploy_skip(self) -> str | None:
        if self.skip_deploy:
            return "skip_deploy set"
        if not (self.deploy_files or self.deploy_groups):
            return "nothing to deploy"
        return None

    def run(self) -> NoReturn:
        with Ticket(
            self.name, self.host, results_dir=self.results_dir,
            worktree=self.worktree, wapi_version=self.wapi_version,
            state=self.initial_state,
        ) as t:
            self._t = t
            connect = t.phase("1. connect", t.connect)
            deployed = t.phase(
                "2. deploy", self.deploy, needs=connect, skip=self._deploy_skip(),
            )
            preflight = t.phase(
                "3. preflight", self.preflight, needs=[connect, deployed],
            )
            steps = [
                (f"{n}. {meta[2]}", meta[0], meta[1], getattr(self, attr))
                for n, (attr, meta) in enumerate(self._steps(), FIRST_STEP_NUMBER)
            ]
            t.steps(
                steps, probe=self.probe, needs=preflight,
                home=None if self.skip_home else ("home", self.home_state, self.home),
            )

