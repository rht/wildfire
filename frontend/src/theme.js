import { createTheme } from "@mui/material/styles";
import typography from "../vendor/mantis/typography";
export const theme = createTheme({
  palette: {
    primary: { main: "#1677ff" },
    background: { default: "#ffffff", paper: "#fff" },
    text: { primary: "#262626", secondary: "#595959" },
    divider: "#f0f0f0",
    error: { main: "#cf1322" },
    warning: { main: "#d48806" },
    success: { main: "#389e0d" },
  },
  typography: typography('"Public Sans", sans-serif'),
  shape: { borderRadius: 4 },
  components: {
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: { root: { textTransform: "none" } },
    },
    MuiCard: {
      styleOverrides: {
        root: { boxShadow: "none", border: "1px solid #e6ebf1" },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        head: { background: "#fafafa", fontWeight: 600, color: "#595959" },
        root: {
          borderBottom: "1px solid #f0f0f0",
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
