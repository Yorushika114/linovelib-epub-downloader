from pathlib import Path


APP = (Path(__file__).parents[1] / "wpf" / "LinovelibDesktop" / "App.xaml.cs").read_text(
    encoding="utf-8"
)


def test_wpf_starts_only_one_instance_and_activates_the_existing_window():
    assert "new Mutex(true, SingleInstanceMutexName, out var createdNew)" in APP
    assert "if (!createdNew)" in APP
    assert "ActivateExistingWindow();" in APP
    assert "Shutdown();" in APP
    assert "SetForegroundWindow" in APP
    assert "if (_ownsSingleInstanceMutex) _singleInstanceMutex?.ReleaseMutex();" in APP
