import subprocess
import time
from pathlib import Path

import regex as re


def run_diagnostics(console):
    """Run system diagnostics to identify potential bottlenecks"""
    console.print("\n[bold cyan]Running system diagnostics...[/bold cyan]")

    # Check available tools
    tools = ["ionice", "iostat", "iotop", "dd"]
    available_tools = {}
    for tool in tools:
        available_tools[tool] = (
            subprocess.run(["which", tool], capture_output=True).returncode == 0
        )

    console.print("\n[bold]Available system tools:[/bold]")
    for tool, available in available_tools.items():
        status = "[green]✓[/green]" if available else "[red]✗[/red]"
        console.print(f"  {status} {tool}")

    # Check disk I/O performance with a quick test
    console.print("\n[bold]Running disk I/O test...[/bold]")
    if available_tools.get("dd"):
        try:
            test_file = Path("./io_test.tmp")
            start_time = time.time()

            # Write test
            subprocess.run(
                [
                    "dd",
                    "if=/dev/zero",
                    f"of={test_file}",
                    "bs=1M",
                    "count=10",
                    "oflag=direct",
                ],
                capture_output=True,
                check=True,
            )

            write_time = time.time() - start_time
            write_speed = 10 / write_time  # MB/s

            # Read test
            start_time = time.time()
            subprocess.run(
                ["dd", f"if={test_file}", "of=/dev/null", "bs=1M", "iflag=direct"],
                capture_output=True,
                check=True,
            )

            read_time = time.time() - start_time
            read_speed = 10 / read_time  # MB/s

            # Cleanup
            test_file.unlink()

            console.print(f"  Write speed: [cyan]{write_speed:.1f} MB/s[/cyan]")
            console.print(f"  Read speed: [cyan]{read_speed:.1f} MB/s[/cyan]")

            if write_speed < 10 or read_speed < 20:
                console.print(
                    "  [yellow]⚠ Disk I/O is quite slow - this may be the bottleneck[/yellow]"
                )
            else:
                console.print("  [green]✓ Disk I/O seems reasonable[/green]")

        except Exception as e:
            console.print(f"  [red]Could not run I/O test: {e}[/red]")
    else:
        console.print("  [yellow]dd not available - skipping I/O test[/yellow]")

    # Check filesystem type
    console.print("\n[bold]Filesystem information:[/bold]")
    try:
        result = subprocess.run(
            ["df", "-T", "."], capture_output=True, text=True, check=True
        )
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            fields = lines[1].split()
            fs_type = fields[1] if len(fields) >= 2 else "unknown"
            console.print(f"  Filesystem type: [cyan]{fs_type}[/cyan]")

            # Warn about network filesystems
            if fs_type in ["nfs", "cifs", "sshfs"]:
                console.print(
                    "  [yellow]⚠ Network filesystem detected - this can be slow[/yellow]"
                )
    except Exception as e:
        console.print(f"  [red]Could not get filesystem info: {e}[/red]")

    # Check memory and swap (Linux only)
    console.print("\n[bold]Memory information:[/bold]")
    try:
        if Path("/proc/meminfo").exists():
            with open("/proc/meminfo") as f:
                meminfo = f.read()

            # Extract key values
            mem_total = re.search(r"MemTotal:\s+(\d+)", meminfo)
            mem_available = re.search(r"MemAvailable:\s+(\d+)", meminfo)
            swap_total = re.search(r"SwapTotal:\s+(\d+)", meminfo)

            if mem_total and mem_available:
                total_mb = int(mem_total.group(1)) // 1024
                available_mb = int(mem_available.group(1)) // 1024
                console.print(
                    f"  RAM: [cyan]{available_mb} MB available / {total_mb} MB total[/cyan]"
                )

            if swap_total:
                swap_mb = int(swap_total.group(1)) // 1024
                console.print(f"  Swap: [cyan]{swap_mb} MB[/cyan]")
        else:
            console.print(
                "  [yellow]/proc/meminfo not available (non-Linux system)[/yellow]"
            )

    except Exception as e:
        console.print(f"  [red]Could not get memory info: {e}[/red]")

    # Check current I/O load if iostat is available
    if available_tools.get("iostat"):
        console.print("\n[bold]Current I/O statistics:[/bold]")
        try:
            result = subprocess.run(
                ["iostat", "-x", "1", "1"], capture_output=True, text=True, check=True
            )
            lines = result.stdout.strip().split("\n")
            # Look for device lines with high utilization
            for line in lines:
                if re.match(r"^[a-z]+", line) and "%util" not in line:
                    fields = line.split()
                    if len(fields) >= 10:
                        device = fields[0]
                        util = (
                            fields[-1] if fields[-1].replace(".", "").isdigit() else "0"
                        )
                        console.print(f"  {device}: [cyan]{util}% utilization[/cyan]")
        except Exception as e:
            console.print(f"  [red]Could not get I/O stats: {e}[/red]")

    console.print("\n[bold]Optimization recommendations:[/bold]")
    console.print(
        "1. If using network/cloud storage, consider moving files to local SSD"
    )
    console.print("2. Use 'iotop -ao' during extraction to monitor I/O usage")
    console.print("3. Consider processing files in batches or using faster storage")
    console.print("4. For cloud VPS: check if you can upgrade to SSD storage")
    console.print("5. Use '--interactive' mode to select smaller subtitle tracks first")
