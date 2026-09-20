import { createTheme } from "@mui/material/styles";
import typography from "../vendor/mantis/typography";
export const theme = createTheme({
  palette: {
    mode: "dark",
    primary: { main: "#ff974f", contrastText: "#100b06" },
    background: { default: "#000000", paper: "#111315" },
    text: { primary: "#f4f4f2", secondary: "#afb5b9" },
    divider: "#303438",
    error: { main: "#ff7770" },
    warning: { main: "#f3bf62", contrastText: "#100b06" },
    success: { main: "#8ed69f" },
    info: { main: "#82bdcf" },
  },
  typography: typography('"Public Sans", sans-serif'),
  shape: { borderRadius: 5 },
  components: {
    MuiPaper: { styleOverrides: { root: { backgroundImage: "none" } } },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: { root: { textTransform: "none" } },
    },
    MuiCard: {
      styleOverrides: {
        root: { boxShadow: "none", border: "1px solid #303438" },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        head: { background: "#1b1e20", fontWeight: 600, color: "#d1d5d7" },
        root: {
          borderBottom: "1px solid #303438",
          padding: "14px 16px",
          fontSize: 13,
        },
      },
    },
    MuiChip: {
      styleOverrides: { root: { borderRadius: 4, height: 24, fontSize: 12 } },
    },
  },
});
