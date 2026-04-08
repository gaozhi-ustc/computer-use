"""Install/uninstall the Workflow Recorder as a Windows service.

Usage:
    python setup_service.py install [--config path/to/config.yaml]
    python setup_service.py uninstall
    python setup_service.py start
    python setup_service.py stop

Requires pywin32 on Windows. On other platforms, prints instructions for
alternative service management (systemd, launchd, nssm).
"""

from __future__ import annotations

import sys


def main():
    if sys.platform != "win32":
        print("Windows service installation is only supported on Windows.")
        print()
        print("Alternatives:")
        print("  macOS:  Use launchd — create a plist in ~/Library/LaunchAgents/")
        print("  Linux:  Use systemd — create a .service unit file")
        print("  Any OS: Use nssm (https://nssm.cc/) to wrap as a service:")
        print('    nssm install WorkflowRecorder python -m workflow_recorder -c config.yaml')
        print()
        print("For development, run directly:")
        print("  python -m workflow_recorder -c config.yaml")
        return

    try:
        import win32serviceutil
        import win32service
        import win32event
        import servicemanager
    except ImportError:
        print("pywin32 is required. Install with: pip install pywin32")
        return

    class WorkflowRecorderService(win32serviceutil.ServiceFramework):
        _svc_name_ = "WorkflowRecorder"
        _svc_display_name_ = "Workflow Recorder Daemon"
        _svc_description_ = "Records desktop workflows via periodic screenshots and GPT vision analysis"

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self.daemon = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self.stop_event)
            if self.daemon:
                self.daemon.stop()

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )
            from workflow_recorder.config import load_config
            from workflow_recorder.daemon import Daemon
            from workflow_recorder.utils.logging import setup_logging

            # Look for config in the service's working directory
            config = load_config("config.yaml")
            setup_logging(
                level=config.logging.level,
                log_file=config.logging.file,
            )
            self.daemon = Daemon(config)
            self.daemon.run()

    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(WorkflowRecorderService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(WorkflowRecorderService)


if __name__ == "__main__":
    main()
