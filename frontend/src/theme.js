import { createTheme } from "@mui/material/styles";
import typography from "../vendor/mantis/typography";
export const theme = createTheme({
  palette: {
    mode: "light",
    primary: { main: "#a84609", contrastText: "#ffffff" },
    background: { default: "#ffffff", paper: "#ffffff" },
    text: { primary: "#20262d", secondary: "#4f5b66" },
    divider: "#dce1e5",
    error: { main: "#bb2926" },
    warning: { main: "#855100", contrastText: "#ffffff" },
    success: { main: "#28733a" },
    info: { main: "#176b89" },
  },
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
        root: { boxShadow: "none", border: "1px solid #dce1e5" },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        head: { background: "#f5f7f8", fontWeight: 600, color: "#35414b" },
        root: {
          borderBottom: "1px solid #dce1e5",
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
