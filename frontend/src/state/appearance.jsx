import {
  createContext,
  useContext,
  useLayoutEffect,
  useMemo,
  useState,
} from "react";
import { CssBaseline, ThemeProvider } from "@mui/material";
import { themes } from "../theme";

const AppearanceContext = createContext(null);
const storageKey = "responsara-theme";

function savedMode() {
  try {
    return window.localStorage.getItem(storageKey) === "dark"
      ? "dark"
      : "light";
  } catch {
    return "light";
  }
}

export function AppearanceProvider({ children }) {
  const [mode, setMode] = useState(savedMode);
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = mode;
    try {
      window.localStorage.setItem(storageKey, mode);
    } catch {
      // The current session remains usable when browser storage is unavailable.
    }
  }, [mode]);
  const value = useMemo(
    () => ({
      mode,
      toggle: () =>
        setMode((current) => (current === "light" ? "dark" : "light")),
    }),
    [mode],
  );
  return (
    <AppearanceContext.Provider value={value}>
      <ThemeProvider theme={themes[mode]}>
        <CssBaseline />
        {children}
      </ThemeProvider>
    </AppearanceContext.Provider>
  );
}

export const useAppearance = () => useContext(AppearanceContext);
