import { IconButton } from "@mui/material";
import MoonOutlined from "@ant-design/icons/MoonOutlined";
import SunOutlined from "@ant-design/icons/SunOutlined";
import { useAppearance } from "../state/appearance";

export default function ThemeToggle() {
  const { mode, toggle } = useAppearance();
  const label = `Switch to ${mode === "light" ? "dark" : "light"} theme`;
  return (
    <IconButton
      className="theme-toggle"
      aria-label={label}
      title={label}
      onClick={toggle}
    >
      {mode === "light" ? <MoonOutlined /> : <SunOutlined />}
    </IconButton>
  );
}
