"""Derived-column pipeline on a synthetic tetrax-style spectrum dataframe."""
import numpy as np
import pandas as pd
import pytest

import df_manipulation as dm


def make_spectrum_df(num_modes=12, num_k=21, kmax=20e6):
    # tetrax always computes a symmetric grid -kmax..kmax; kMin only filters rows later
    k = np.linspace(-kmax, kmax, num_k)
    df = pd.DataFrame({'m': np.zeros(num_k), 'k (rad/m)': k})
    for n in range(num_modes):
        df[f'f{n} (Hz)'] = (n + 1) * 1e9 + 0.1e9 * (k / 1e6) ** 2
    for n in range(num_modes):
        df[f'Gamma{n} (Hz)'] = np.full(num_k, (n + 1) * 1e6)
    return df


@pytest.fixture
def polished(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, 'SIMULATION_DATA_PATH', str(tmp_path))
    (tmp_path / 'test-task').mkdir()
    df = make_spectrum_df()
    df = dm.lifetime(df)
    df = dm.group_velocity(df)
    df = dm.propagation_length(df)
    df = dm.dataframe_polish(df, kmin=0, kmax=20, task_id='test-task')
    return df, tmp_path / 'test-task' / 'dispersion_data.csv'


def test_all_modes_get_distinct_derived_columns(polished):
    df, _ = polished
    for n in range(12):
        assert f'f{n} (GHz)' in df.columns, f'missing f{n}'
        assert f'v{n} (m/s)' in df.columns, f'missing v{n} (mode index >= 10 breaks single-char parsing)'
        assert f'lt{n} (ns)' in df.columns, f'missing lt{n}'
        assert f'pl{n} (µm)' in df.columns, f'missing pl{n}'
    assert len(set(df.columns)) == len(df.columns)


def test_gamma_columns_dropped(polished):
    df, _ = polished
    assert not [c for c in df.columns if 'Gamma' in c]


def test_k_filtered_to_requested_range(polished):
    df, _ = polished
    assert df['k (rad/µm)'].min() >= 0
    assert df['k (rad/µm)'].max() <= 20
    assert len(df) == 11


def test_group_velocity_zero_inserted_at_k_zero(polished):
    df, _ = polished
    # After filtering to k >= 0, the injected v=0 sample sits at kshift=0 (first row)
    first = df.iloc[0]
    assert first['kshift (rad/µm)'] == 0.0
    assert first['v0 (m/s)'] == 0.0


def test_csv_written_without_index_column(polished):
    _, csv_path = polished
    header = csv_path.read_text().splitlines()[0]
    assert header.split(',')[0] == 'k (rad/µm)'
    assert 'Unnamed' not in header


def test_lifetime_values(polished):
    df, _ = polished
    # lt_n = 1/Gamma_n * 1e9 / (2*pi), Gamma_n = (n+1) MHz
    expected = 1 / 1e6 * 1e9 / (2 * np.pi)
    assert np.allclose(df['lt0 (ns)'], expected)
    assert np.allclose(df['lt9 (ns)'], expected / 10)
    assert np.allclose(df['lt11 (ns)'], expected / 12)


def test_if_nan_detection():
    df = make_spectrum_df(num_modes=2, num_k=5)
    assert dm.if_nan(df) is False
    df.loc[2, 'f1 (Hz)'] = np.nan
    assert dm.if_nan(df) is True
