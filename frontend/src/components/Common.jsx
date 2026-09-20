// MainCard and metric layout adapted from CodedThemes Mantis. See vendor/mantis/LICENSE.
import {
  Card,
  CardHeader,
  CardContent,
  Divider,
  Typography,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  IconButton,
  Button,
} from "@mui/material";
import CloseOutlined from "@ant-design/icons/CloseOutlined";
import { Link } from "react-router-dom";
import { count, humanize, gps } from "../state/model.mjs";
export function MainCard({
  title,
  action,
  children,
  className = "",
  content = true,
}) {
  return (
    <Card className={className}>
      {title && (
        <>
          <CardHeader
            title={title}
            action={action}
            slotProps={{ title: { variant: "subtitle1", component: "h2" } }}
            sx={{ p: 1.5 }}
          />
          <Divider />
        </>
      )}
      {content ? (
        <CardContent sx={{ p: 1.5, "&:last-child": { pb: 1.5 } }}>
          {children}
        </CardContent>
      ) : (
        children
      )}
    </Card>
  );
}
export function Metric({
  label,
  value,
  valueText,
  detail,
  note,
  to,
  icon: Icon,
  tone = "blue",
  testId,
  compact = false,
}) {
  const body = (
    <>
      <div className="metric-top">
        <span>{label}</span>
        {Icon && <Icon aria-hidden="true" className={`metric-icon ${tone}`} />}
      </div>
      <div className="metric-value">{valueText ?? count(value)}</div>
      {detail && <div className="metric-detail">{detail}</div>}
      {!compact && note && <div className="metric-note">{note}</div>}
      {to && !compact && <span className="metric-open">View details</span>}
    </>
  );
  return (
    <Card className={`metric${compact ? " compact" : ""}`} data-testid={testId}>
      {to ? (
        <Link to={to}>{body}</Link>
      ) : (
        <div className="metric-body">{body}</div>
      )}
    </Card>
  );
}
export function Status({ value }) {
  const text = humanize(value),
    color = /high|exhaust|failed|fire intersects/i.test(text)
      ? "error"
      : /assist|human|review|moderate|unknown|proposed|not assessed/i.test(text)
        ? "warning"
        : /active|completed|arrived|confirmed|self evacuating|connected/i.test(
              text,
            )
          ? "success"
          : "default";
  return (
    <Chip
      className="status-tag"
      title={text}
      label={text}
      color={color}
      variant="outlined"
      size="small"
    />
  );
}
export function Empty({ title = "No records available", children }) {
  return (
    <div className="empty">
      <Typography variant="h5">{title}</Typography>
      <Typography color="text.secondary" sx={{ mt: 1 }}>
        {children || "Records will appear when they are supplied."}
      </Typography>
    </div>
  );
}
export function DetailDialog({ title, open, onClose, children }) {
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle sx={{ pr: 7 }}>
        {title}
        <IconButton
          aria-label="Close details"
          onClick={onClose}
          sx={{ position: "absolute", right: 12, top: 12 }}
        >
          <CloseOutlined />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers>{children}</DialogContent>
    </Dialog>
  );
}
export function Facts({ rows }) {
  return (
    <dl className="facts">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value ?? "Not supplied"}</dd>
        </div>
      ))}
    </dl>
  );
}
export function PageHeading({ title, description, action }) {
  return (
    <div className="page-heading">
      <div>
        <Typography variant="h3" component="h1">
          {title}
        </Typography>
        {description && (
          <Typography color="text.secondary" sx={{ mt: 0.75 }}>
            {description}
          </Typography>
        )}
      </div>
      {action}
    </div>
  );
}
export function FilterSelect({ label, value, onChange, options }) {
  return (
    <label className="filter-field">
      <span>{label}</span>
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map(([v, text]) => (
          <option key={v} value={v}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}
export function FilterInput({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
}) {
  return (
    <label className="filter-field">
      <span>{label}</span>
      <input
        aria-label={label}
        value={value}
        type={type}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}
export function ClearFilters({ onClick }) {
  return (
    <Button onClick={onClick} size="small">
      Reset filters
    </Button>
  );
}

export function Coordinates({ location }) {
  return (
    <div className="small-muted gps">
      GPS: {gps(location)}
      {gps(location) !== "Not supplied" && " · lat, lon"}
    </div>
  );
}
