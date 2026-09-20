import { createTheme } from "@mui/material/styles";
import typography from "../vendor/mantis/typography";
const lightPalette = {
  mode: "light",
  primary: { main: "#a84609", contrastText: "#ffffff" },
  background: { default: "#ffffff", paper: "#ffffff" },
  text: { primary: "#20262d", secondary: "#4f5b66" },
  divider: "#dce1e5",
  error: { main: "#bb2926" },
  warning: { main: "#855100", contrastText: "#ffffff" },
  success: { main: "#28733a" },
  info: { main: "#176b89" },
};
const darkPalette = {
  mode: "dark",
  primary: { main: "#ffb272", contrastText: "#20262d" },
  background: { default: "#141a21", paper: "#1c242e" },
  text: { primary: "#e7edf2", secondary: "#b4bfca" },
  divider: "#3b4856",
  error: { main: "#ff908c" },
  warning: { main: "#f4c16c", contrastText: "#20262d" },
  success: { main: "#8ad59a" },
  info: { main: "#83c9e7" },
};
function makeTheme(palette) {
  return createTheme({
    palette,
    typography: typography('"Public Sans", sans-serif'),
    shape: { borderRadius: 5 },
    components: {
      MuiPaper: { styleOverrides: { root: { backgroundImage: "none" } } },
      MuiButton: {
        defaultProps: { disableElevation: true },
        styleOverrides: { root: { textTransform: "none", minHeight: 44 } },
      },
      MuiIconButton: {
        styleOverrides: { root: { minWidth: 44, minHeight: 44 } },
      },
      MuiCard: {
        styleOverrides: {
          root: { boxShadow: "none", border: `1px solid ${palette.divider}` },
        },
      },
      MuiTableCell: {
        styleOverrides: {
          head: {
            background: palette.mode === "dark" ? "#263240" : "#f5f7f8",
            fontWeight: 600,
            color: palette.mode === "dark" ? "#dbe4ed" : "#35414b",
          },
          root: {
            borderBottom: `1px solid ${palette.divider}`,
            padding: "10px 12px",
            fontSize: 13,
          },
        },
      },
      MuiChip: {
        styleOverrides: { root: { borderRadius: 4, height: 24, fontSize: 12 } },
      },
    },
  });
}
export const themes = {
  light: makeTheme(lightPalette),
  dark: makeTheme(darkPalette),
};
