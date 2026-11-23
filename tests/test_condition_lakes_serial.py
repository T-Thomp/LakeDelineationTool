"""Serial conditionLakes launch: one core must not import mpi4py."""

import builtins

from conditionLakes import SerialComm, init_comm, mpi_launch_rank_size


def test_serial_comm_passes_payloads_through():
    comm = SerialComm()
    payload = {"lakes": [1, 2]}
    assert comm.Get_rank() == 0
    assert comm.Get_size() == 1
    assert comm.bcast(payload, root=0) is payload
    assert comm.scatter([[3, 4]], root=0) == [3, 4]
    assert comm.gather("patch", root=0) == ["patch"]


def test_plain_python_on_a_slurm_allocation_stays_serial(monkeypatch):
    """The batch shell inherits the job's task count. That is not a launch."""
    monkeypatch.setenv("SLURM_STEP_ID", "0")
    monkeypatch.setenv("SLURM_NTASKS", "250")
    monkeypatch.setenv("PMI_SIZE", "250")
    monkeypatch.setenv("PMI_RANK", "0")
    monkeypatch.delenv("SLURM_PROCID", raising=False)
    monkeypatch.delenv("OMPI_COMM_WORLD_SIZE", raising=False)
    monkeypatch.delenv("OMPI_COMM_WORLD_RANK", raising=False)

    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "mpi4py" or name.startswith("mpi4py."):
            raise AssertionError("serial path imported mpi4py")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert mpi_launch_rank_size() == (0, 1)
    assert isinstance(init_comm(), SerialComm)


def test_srun_step_reports_its_task_rank(monkeypatch):
    monkeypatch.delenv("OMPI_COMM_WORLD_SIZE", raising=False)
    monkeypatch.setenv("SLURM_PROCID", "3")
    monkeypatch.setenv("SLURM_STEP_NUM_TASKS", "16")
    monkeypatch.setenv("SLURM_NTASKS", "250")
    assert mpi_launch_rank_size() == (3, 16)
