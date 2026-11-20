(() => {
  const api = window.pywebview.api;
  window.talyxDesktop = Object.freeze({
    openWorkspaceWindow: workspaceId => api.open_workspace(workspaceId),
    onWorkspaceRequested: callback => {
      const listener = event => callback(event.detail);
      window.addEventListener('talyx-desktop-workspace', listener);
      return () => window.removeEventListener('talyx-desktop-workspace', listener);
    },
    onExitRequested: callback => {
      const listener = event => callback(event.detail);
      window.addEventListener('talyx-desktop-exit', listener);
      void api.listen_exit(true).then(request => { if (request) callback(request); });
      return () => {
        window.removeEventListener('talyx-desktop-exit', listener);
        void api.listen_exit(false);
      };
    },
    minimizeWindow: () => api.minimize_window(),
    capturePanel: (workspace, panel) => api.capture_panel(workspace, panel),
    toggleMaximizeWindow: () => api.toggle_maximize_window(),
    closeWindow: () => api.close_window(),
    isWindowMaximized: () => api.window_maximized(),
    onWindowStateChanged: callback => {
      const listener = event => callback(event.detail);
      window.addEventListener('talyx-desktop-window', listener);
      return () => window.removeEventListener('talyx-desktop-window', listener);
    },
    dragWindow: () => api.drag_window(),
    resizeWindow: edge => api.resize_window(edge),
    openFolder: path => api.open_folder(path),
    requestExit: () => api.request_exit(),
    confirmExit: requestId => api.confirm_exit(requestId),
    cancelExit: requestId => api.cancel_exit(requestId),
  });
  window.dispatchEvent(new Event('talyx-desktop-ready'));
})();
