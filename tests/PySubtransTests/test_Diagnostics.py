"""Characterization tests for run_diagnostics: they pin the console output and the
commands it runs, with every system call faked."""

import subprocess
from unittest.mock import MagicMock, call, mock_open, patch

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.MKV import Diagnostics
from PySubtrans.MKV.Diagnostics import run_diagnostics

ALL_TOOLS = {"ionice", "iostat", "iotop", "dd"}

DF_EXT4 = (
    "Filesystem     Type 1K-blocks     Used Available Use% Mounted on\n"
    "/dev/sda1      ext4 100000000 50000000  50000000  50% /\n"
)
DF_NFS = (
    "Filesystem          Type 1K-blocks  Used Available Use% Mounted on\n"
    "server:/export      nfs  100000000 10000 99990000   1% /mnt\n"
)
DF_HEADER_ONLY = "Filesystem     Type 1K-blocks     Used Available Use% Mounted on\n"

MEMINFO_FULL = (
    "MemTotal:       24576000 kB\n"
    "MemFree:         1024000 kB\n"
    "MemAvailable:   12288000 kB\n"
    "SwapTotal:       2048000 kB\n"
)
MEMINFO_NO_AVAILABLE = "MemTotal:       24576000 kB\nSwapTotal:       1024000 kB\n"

IOSTAT_OUTPUT = (
    "Linux 6.8.0 (host) \t11/17/2026 \t_aarch64_\t(4 CPU)\n"
    "\n"
    "avg-cpu:  %user   %nice %system %iowait  %steal   %idle\n"
    "           1.00    0.00    0.50    0.10    0.00   98.40\n"
    "\n"
    "Device            r/s     rkB/s   rrqm/s  %rrqm r_await rareq-sz     w/s"
    "     wkB/s   %util\n"
    "sda              1.00     10.00     0.00   0.00    0.50    10.00    2.00"
    "     20.00   12.50\n"
    "sdb              1.00     10.00     0.00   0.00    0.50    10.00    2.00"
    "     20.00   n/a\n"
    "loop0            0.10      0.20\n"
)

RECOMMENDATIONS = [
    "\n[bold]Optimization recommendations:[/bold]",
    "1. If using network/cloud storage, consider moving files to local SSD",
    "2. Use 'iotop -ao' during extraction to monitor I/O usage",
    "3. Consider processing files in batches or using faster storage",
    "4. For cloud VPS: check if you can upgrade to SSD storage",
    "5. Use '--interactive' mode to select smaller subtitle tracks first",
]

DD_WRITE = [
    "dd",
    "if=/dev/zero",
    "of=io_test.tmp",
    "bs=1M",
    "count=10",
    "oflag=direct",
]
DD_READ = ["dd", "if=io_test.tmp", "of=/dev/null", "bs=1M", "iflag=direct"]


class FakeSystem:
    """Answers the commands run_diagnostics runs, without touching the host."""

    def __init__(
        self,
        tools: set[str],
        df_stdout: str = DF_EXT4,
        iostat_stdout: str = IOSTAT_OUTPUT,
        failing: set[str] | None = None,
    ) -> None:
        self.tools = tools
        self.df_stdout = df_stdout
        self.iostat_stdout = iostat_stdout
        self.failing = failing or set()

    def run(self, args: list[str], **kwargs) -> subprocess.CompletedProcess:
        name = args[0]
        if name == "which":
            return subprocess.CompletedProcess(args, 0 if args[1] in self.tools else 1)
        if name in self.failing:
            raise OSError(f"{name} failed")
        if name == "df":
            return subprocess.CompletedProcess(args, 0, stdout=self.df_stdout)
        if name == "iostat":
            return subprocess.CompletedProcess(args, 0, stdout=self.iostat_stdout)
        return subprocess.CompletedProcess(args, 0, stdout="")


class TestRunDiagnostics(LoggedTestCase):
    def _run(
        self,
        system: FakeSystem,
        times: list[float],
        meminfo: str | None,
        open_error: bool = False,
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        """Run run_diagnostics against the fake system and return the console,
        subprocess.run and Path mocks."""
        console = MagicMock()
        fake_open = mock_open(read_data=meminfo or "")
        if open_error:
            fake_open.side_effect = OSError("permission denied")
        with (
            patch.object(
                Diagnostics.subprocess, "run", side_effect=system.run
            ) as fake_run,
            patch.object(Diagnostics, "time") as fake_time,
            patch.object(Diagnostics, "Path") as fake_path,
            patch("PySubtrans.MKV.Diagnostics.open", fake_open, create=True),
        ):
            fake_time.time.side_effect = times
            fake_path.return_value.__str__.return_value = "io_test.tmp"
            fake_path.return_value.exists.return_value = meminfo is not None
            run_diagnostics(console)
        return console, fake_run, fake_path

    def _assertPrinted(self, console: MagicMock, expected: list[str]) -> None:
        self.assertLoggedEqual(
            "console calls", [call.print(text) for text in expected], console.mock_calls
        )

    def test_all_tools_fast_disk(self):
        system = FakeSystem(ALL_TOOLS)
        console, fake_run, fake_path = self._run(
            system, [0.0, 0.5, 1.0, 1.25], MEMINFO_FULL
        )
        self._assertPrinted(console, EXPECTED_ALL_TOOLS_FAST_DISK)
        self.assertLoggedEqual(
            "commands run",
            [
                call(["which", "ionice"], capture_output=True),
                call(["which", "iostat"], capture_output=True),
                call(["which", "iotop"], capture_output=True),
                call(["which", "dd"], capture_output=True),
                call(DD_WRITE, capture_output=True, check=True),
                call(DD_READ, capture_output=True, check=True),
                call(["df", "-T", "."], capture_output=True, text=True, check=True),
                call(
                    ["iostat", "-x", "1", "1"],
                    capture_output=True,
                    text=True,
                    check=True,
                ),
            ],
            fake_run.call_args_list,
        )
        fake_path.return_value.unlink.assert_called_once_with()
        self.assertLoggedEqual(
            "paths built",
            [call("./io_test.tmp"), call("/proc/meminfo")],
            [c for c in fake_path.mock_calls if c[0] == ""],
        )

    def test_no_tools(self):
        system = FakeSystem(set(), df_stdout=DF_HEADER_ONLY)
        console, fake_run, fake_path = self._run(system, [], None)
        self._assertPrinted(console, EXPECTED_NO_TOOLS)
        self.assertLoggedEqual(
            "commands run",
            [
                call(["which", "ionice"], capture_output=True),
                call(["which", "iostat"], capture_output=True),
                call(["which", "iotop"], capture_output=True),
                call(["which", "dd"], capture_output=True),
                call(["df", "-T", "."], capture_output=True, text=True, check=True),
            ],
            fake_run.call_args_list,
        )
        fake_path.return_value.unlink.assert_not_called()

    def test_slow_disk_network_filesystem(self):
        system = FakeSystem({"dd"}, df_stdout=DF_NFS)
        console, _, _ = self._run(system, [0.0, 2.0, 2.0, 3.0], MEMINFO_NO_AVAILABLE)
        self._assertPrinted(console, EXPECTED_SLOW_DISK_NETWORK_FS)

    def test_every_phase_fails(self):
        system = FakeSystem(ALL_TOOLS, failing={"dd", "df", "iostat"})
        console, _, fake_path = self._run(system, [0.0], MEMINFO_FULL, open_error=True)
        self._assertPrinted(console, EXPECTED_EVERY_PHASE_FAILS)
        fake_path.return_value.unlink.assert_not_called()


EXPECTED_ALL_TOOLS_FAST_DISK: list[str] = [
    "\n[bold cyan]Running system diagnostics...[/bold cyan]",
    "\n[bold]Available system tools:[/bold]",
    "  [green]✓[/green] ionice",
    "  [green]✓[/green] iostat",
    "  [green]✓[/green] iotop",
    "  [green]✓[/green] dd",
    "\n[bold]Running disk I/O test...[/bold]",
    "  Write speed: [cyan]20.0 MB/s[/cyan]",
    "  Read speed: [cyan]40.0 MB/s[/cyan]",
    "  [green]✓ Disk I/O seems reasonable[/green]",
    "\n[bold]Filesystem information:[/bold]",
    "  Filesystem type: [cyan]ext4[/cyan]",
    "\n[bold]Memory information:[/bold]",
    "  RAM: [cyan]12000 MB available / 24000 MB total[/cyan]",
    "  Swap: [cyan]2000 MB[/cyan]",
    "\n[bold]Current I/O statistics:[/bold]",
    "  sda: [cyan]12.50% utilization[/cyan]",
    "  sdb: [cyan]0% utilization[/cyan]",
    *RECOMMENDATIONS,
]
EXPECTED_NO_TOOLS: list[str] = [
    "\n[bold cyan]Running system diagnostics...[/bold cyan]",
    "\n[bold]Available system tools:[/bold]",
    "  [red]✗[/red] ionice",
    "  [red]✗[/red] iostat",
    "  [red]✗[/red] iotop",
    "  [red]✗[/red] dd",
    "\n[bold]Running disk I/O test...[/bold]",
    "  [yellow]dd not available - skipping I/O test[/yellow]",
    "\n[bold]Filesystem information:[/bold]",
    "\n[bold]Memory information:[/bold]",
    "  [yellow]/proc/meminfo not available (non-Linux system)[/yellow]",
    *RECOMMENDATIONS,
]
EXPECTED_SLOW_DISK_NETWORK_FS: list[str] = [
    "\n[bold cyan]Running system diagnostics...[/bold cyan]",
    "\n[bold]Available system tools:[/bold]",
    "  [red]✗[/red] ionice",
    "  [red]✗[/red] iostat",
    "  [red]✗[/red] iotop",
    "  [green]✓[/green] dd",
    "\n[bold]Running disk I/O test...[/bold]",
    "  Write speed: [cyan]5.0 MB/s[/cyan]",
    "  Read speed: [cyan]10.0 MB/s[/cyan]",
    "  [yellow]⚠ Disk I/O is quite slow - this may be the bottleneck[/yellow]",
    "\n[bold]Filesystem information:[/bold]",
    "  Filesystem type: [cyan]nfs[/cyan]",
    "  [yellow]⚠ Network filesystem detected - this can be slow[/yellow]",
    "\n[bold]Memory information:[/bold]",
    "  Swap: [cyan]1000 MB[/cyan]",
    *RECOMMENDATIONS,
]
EXPECTED_EVERY_PHASE_FAILS: list[str] = [
    "\n[bold cyan]Running system diagnostics...[/bold cyan]",
    "\n[bold]Available system tools:[/bold]",
    "  [green]✓[/green] ionice",
    "  [green]✓[/green] iostat",
    "  [green]✓[/green] iotop",
    "  [green]✓[/green] dd",
    "\n[bold]Running disk I/O test...[/bold]",
    "  [red]Could not run I/O test: dd failed[/red]",
    "\n[bold]Filesystem information:[/bold]",
    "  [red]Could not get filesystem info: df failed[/red]",
    "\n[bold]Memory information:[/bold]",
    "  [red]Could not get memory info: permission denied[/red]",
    "\n[bold]Current I/O statistics:[/bold]",
    "  [red]Could not get I/O stats: iostat failed[/red]",
    *RECOMMENDATIONS,
]
