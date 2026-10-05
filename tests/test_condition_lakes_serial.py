"""Serial conditionLakes launch: one core must not import mpi4py."""

import builtins

import pytest

from conditionLakes import (
    SerialComm,
    init_comm,
    mpi_launch_rank_size,
    requested_worker_count,
)


def test_serial_comm_passes_payloads_through():
    comm = SerialComm()
    payload = {"lakes": [1, 2]}
    assert comm.Get_rank() == 0
    assert comm.Get_size() == 1
    assert comm.bcast(payload, root=0) is payload
    assert comm.scatter([[3, 4]], root=0) == [3, 4]
    assert comm.gather("patch", root=0) == ["patch"]


def test_explicit_ncores_overrides_env(monkeypatch):
    monkeypatch.setenv("FLOWPATH_NCORES", "16")
    assert requested_worker_count(1) == 1
    assert requested_worker_count(None) == 16
    with pytest.raises(ValueError):
        requested_worker_count(0)


def test_ncores_1_skips_mpi_on_a_slurm_step(monkeypatch):
    """Compute-node shells inherit Slurm PMI vars; serial must ignore them."""
    monkeypatch.setenv("SLURM_STEP_ID", "0")
    monkeypatch.setenv("PMI_SIZE", "250")
    monkeypatch.setenv("PMI_RANK", "0")
    monkeypatch.delenv("OMPI_COMM_WORLD_SIZE", raising=False)
    monkeypatch.delenv("OMPI_COMM_WORLD_RANK", raising=False)

    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "mpi4py" or name.startswith("mpi4py."):
            raise AssertionError("serial path imported mpi4py")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert mpi_launch_rank_size() == (0, 1)
    assert isinstance(init_comm(1), SerialComm)


def test_ncores_above_1_without_mpirun_exits(monkeypatch):
    monkeypatch.delenv("OMPI_COMM_WORLD_SIZE", raising=False)
    monkeypatch.delenv("PMI_SIZE", raising=False)
    monkeypatch.delenv("SLURM_STEP_ID", raising=False)
    monkeypatch.delenv("SLURM_STEPID", raising=False)
    with pytest.raises(SystemExit) as exc:
        init_comm(4)
    assert exc.value.code == 1


def test_ncores_1_under_mpirun_exits(monkeypatch):
    monkeypatch.setenv("OMPI_COMM_WORLD_SIZE", "4")
    monkeypatch.setenv("OMPI_COMM_WORLD_RANK", "0")
    with pytest.raises(SystemExit) as exc:
        init_comm(1)
    assert exc.value.code == 1
