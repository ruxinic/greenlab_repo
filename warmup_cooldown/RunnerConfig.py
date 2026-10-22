from EventManager.Models.RunnerEvents import RunnerEvents
from EventManager.EventSubscriptionController import EventSubscriptionController
from ConfigValidator.Config.Models.RunTableModel import RunTableModel
from ConfigValidator.Config.Models.FactorModel import FactorModel
from ConfigValidator.Config.Models.RunnerContext import RunnerContext
from ConfigValidator.Config.Models.OperationType import OperationType
from ProgressManager.Output.OutputProcedure import OutputProcedure as output
from Plugins.Profilers.EnergiBridge import EnergiBridge

from typing import Dict, List, Any, Optional
from pathlib import Path
from os.path import dirname, realpath
from datetime import datetime
import os
import subprocess
import sys
import time


class RunnerConfig:
    ROOT_DIR = Path(dirname(realpath(__file__)))
    BENCHMARK_DIR = ROOT_DIR / "benchmarks"

    # ================================ USER SPECIFIC CONFIG ================================
    """The name of the experiment."""
    name:                       str             = "warmup_cooldown"

    """Results are stored in `results_output_path / name`."""
    results_output_path:        Path             = ROOT_DIR / 'experiments'

    """Experiment operation type. Unless you manually want to initiate each run, use `OperationType.AUTO`."""
    operation_type:             OperationType   = OperationType.AUTO

    """Warm-up and cool-down are done inside the run itself (start_run / stop_run),
    so no extra wait is needed between runs."""
    time_between_runs_in_ms:    int             = 0

    # ---- Experiment design (see "Experimental Design" in the paper) ----
    DURATIONS_S:    List[int] = [5, 30, 60, 120, 210]
    REPETITIONS:    int       = 25

    # Pilot / debugging overrides, e.g.
    #   REPETITIONS=1 DURATION_SCALE=0.02 sudo -E python experiment-runner/ warmup_cooldown/RunnerConfig.py
    # DURATION_SCALE shrinks the idle periods only; the recorded `duration` factor keeps its nominal value.
    DURATION_SCALE: float     = float(os.environ.get("DURATION_SCALE", 1.0))
    if "REPETITIONS" in os.environ:
        REPETITIONS = int(os.environ["REPETITIONS"])

    # benchmark name -> arguments appended to `python3 <script>`.
    # The problem sizes below are placeholders: calibrate them in the pilot so a run lasts ~5-60 s.
    # scimark runs through pyperf; `--worker -l N -w 0 -n 1` makes it execute a single
    # worker process doing N loops, with no calibration and no spawned child processes.
    BENCHMARKS: Dict[str, List[str]] = {
        "binary-trees":    ["binary-trees.py",   "16"],
        "n-body":          ["n-body.py",         "500000"],
        "spectral-norm":   ["spectral-norm.py",  "500"],
        "scimark-sor":     ["scimark.py", "sor",             "--worker", "-l", "200", "-w", "0", "-n", "1"],
        "scimark-sparse":  ["scimark.py", "sparse_mat_mult", "--worker", "-l", "50",  "-w", "0", "-n", "1"],
        "scimark-lu":      ["scimark.py", "lu",              "--worker", "-l", "20",  "-w", "0", "-n", "1"],
    }

    def __init__(self):
        """Executes immediately after program start, on config load"""

        EventSubscriptionController.subscribe_to_multiple_events([
            (RunnerEvents.BEFORE_EXPERIMENT, self.before_experiment),
            (RunnerEvents.BEFORE_RUN       , self.before_run       ),
            (RunnerEvents.START_RUN        , self.start_run        ),
            (RunnerEvents.START_MEASUREMENT, self.start_measurement),
            (RunnerEvents.INTERACT         , self.interact         ),
            (RunnerEvents.STOP_MEASUREMENT , self.stop_measurement ),
            (RunnerEvents.STOP_RUN         , self.stop_run         ),
            (RunnerEvents.POPULATE_RUN_DATA, self.populate_run_data),
            (RunnerEvents.AFTER_EXPERIMENT , self.after_experiment )
        ])
        self.run_table_model = None  # Initialized later
        self.profiler = None
        self.run_start = 0.0

        output.console_log("Custom config loaded")

    def create_run_table_model(self) -> RunTableModel:
        """Full factorial duration x benchmark, REPETITIONS each, with the execution order
        shuffled over the whole pool of trials (not per block)."""
        duration_factor  = FactorModel("duration",  self.DURATIONS_S)
        benchmark_factor = FactorModel("benchmark", list(self.BENCHMARKS))
        self.run_table_model = RunTableModel(
            factors      = [duration_factor, benchmark_factor],
            repetitions  = self.REPETITIONS,
            shuffle      = True,
            data_columns = ['energy_j', 'benchmark_time_s', 'total_time_s', 'timestamp']
        )
        return self.run_table_model

    def _idle(self, context: RunnerContext, phase: str) -> None:
        nominal = context.execute_run['duration']
        output.console_log(f"{phase}: idling {nominal}s")
        time.sleep(nominal * self.DURATION_SCALE)

    def _make_results_readable(self) -> None:
        """The runner rewrites run_table.csv through a 0600 temp file, which under `sudo`
        leaves it root-only and unreadable by Excel. Hand the results back to the invoking user."""
        user = os.environ.get("SUDO_USER")
        if os.getuid() != 0 or not user:
            return
        subprocess.run(["chown", "-R", user, str(self.results_output_path)], capture_output=True)
        subprocess.run(["chmod", "-R", "u+rwX,go+rX", str(self.results_output_path)], capture_output=True)

    def before_experiment(self) -> None:
        """EnergiBridge is launched through `sudo`; a password prompt would hang the
        piped subprocess, so fail early if sudo is not usable non-interactively."""
        if os.getuid() != 0 and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
            raise RuntimeError("EnergiBridge needs root. Start the experiment with `sudo -E ...` "
                               "or run `sudo -v` first (and keep the ticket alive).")

    def before_run(self) -> None:
        pass

    def start_run(self, context: RunnerContext) -> None:
        """Warm-up: the machine idles for `duration` seconds before the measurement."""
        self._make_results_readable()
        self.run_start = time.perf_counter()
        self._idle(context, "Warm-up")

    def start_measurement(self, context: RunnerContext) -> None:
        """EnergiBridge wraps a single execution of the assigned benchmark."""
        script, *args = self.BENCHMARKS[context.execute_run['benchmark']]
        target = " ".join([sys.executable, str(self.BENCHMARK_DIR / script), *args])

        self.profiler = EnergiBridge(target_program=target,
                                     out_file=context.run_dir / "energibridge.csv")
        self.profiler.start()

    def interact(self, context: RunnerContext) -> None:
        pass

    def stop_measurement(self, context: RunnerContext) -> None:
        """Block until the benchmark (and so EnergiBridge) finishes."""
        self.profiler.stop(wait=True)

    def stop_run(self, context: RunnerContext) -> None:
        """Cool-down: the machine idles for the same `duration` seconds."""
        self._idle(context, "Cool-down")
        self.total_time_s = time.perf_counter() - self.run_start

    def populate_run_data(self, context: RunnerContext) -> Optional[Dict[str, Any]]:
        _, summary = self.profiler.parse_log(self.profiler.logfile,
                                             self.profiler.summary_logfile)
        if not isinstance(summary, dict):
            raise RuntimeError("Could not parse the EnergiBridge summary; run marked as failed")

        return {"energy_j":         summary["total_joules"],
                "benchmark_time_s": summary["runtime_seconds"],
                # With DURATION_SCALE != 1 this is the real (scaled) wall-clock time
                "total_time_s":     round(self.total_time_s, 3),
                "timestamp":        datetime.now().isoformat(timespec="seconds")}

    def after_experiment(self) -> None:
        self._make_results_readable()

    # ================================ DO NOT ALTER BELOW THIS LINE ================================
    experiment_path:            Path             = None
