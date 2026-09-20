import React from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider, CssBaseline, Alert } from "@mui/material";
import { HashRouter } from "react-router-dom";
import "@fontsource/public-sans/400.css";
import "@fontsource/public-sans/500.css";
import "@fontsource/public-sans/600.css";
import "@fontsource/public-sans/800.css";
import { theme } from "./theme";
import App from "./App";
import "./styles.css";
class Boundary extends React.Component {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <Alert severity="error">
        The dashboard could not render this state. Reload to reconnect.
      </Alert>
    ) : (
      this.props.children
    );
  }
}
createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Boundary>
        <HashRouter>
          <App />
        </HashRouter>
      </Boundary>
    </ThemeProvider>
  </React.StrictMode>,
);
