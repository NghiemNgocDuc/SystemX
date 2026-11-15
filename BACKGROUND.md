# Background operation

SystemAutoBot keeps monitoring when you close its main window. The close button hides the window; it does not quit the app. This also applies when running the live demos.

- Reopen it from the blue SystemAutoBot icon in the Windows tray, or press **Ctrl+Alt+Space**.
- To stop it, right-click that icon and choose **Quit**.
- **Pause fixes** pauses repairs; it does not exit the app.

The packaged Windows installer creates a startup shortcut for the current Windows user. After the initial setup, SystemAutoBot starts quietly in the tray at sign-in. A source checkout is not automatically registered for startup.

It runs during your Windows session while the computer is awake. It is not a Windows service and does not monitor while the PC is asleep, shut down, or signed out.

To verify: open the app, close the main window, then press **Ctrl+Alt+Space**. The same running app returns, with its monitoring and activity intact. The installer and startup behavior can be inspected in `packaging/install.ps1`; no startup registration is needed just to try the demos.
