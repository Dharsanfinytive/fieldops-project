import React, { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

export interface CustomerThemedSelectOption {
  value: string;
  label: string;
}

interface CustomerThemedSelectProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: CustomerThemedSelectOption[];
  placeholder?: string;
  disabled?: boolean;
  ariaLabel?: string;
  compact?: boolean;
  style?: React.CSSProperties;
  menuMaxHeight?: number;
}

export default function CustomerThemedSelect({
  id,
  value,
  onChange,
  options,
  placeholder = "Select...",
  disabled = false,
  ariaLabel,
  compact = false,
  style,
  menuMaxHeight = 240,
}: CustomerThemedSelectProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const selectedOption = options.find((opt) => opt.value === value);

  useEffect(() => {
    if (!open) return;
    const handleOutsideClick = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleOutsideClick);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleOutsideClick);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  return (
    <div
      ref={containerRef}
      style={{
        position: "relative",
        display: "inline-block",
        width: style?.width || "100%",
        ...style,
      }}
    >
      <button
        id={id}
        type="button"
        disabled={disabled}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => {
          if (!disabled) setOpen((prev) => !prev);
        }}
        style={{
          width: "100%",
          minHeight: compact ? "34px" : "42px",
          padding: compact ? "6px 10px" : "9px 12px",
          borderRadius: compact ? "8px" : "10px",
          border: open
            ? "1.5px solid #059669"
            : "1.5px solid #CBD5E1",
          background: disabled ? "#F8FAFC" : open ? "#F0FDF4" : "#FFFFFF",
          color: selectedOption ? "#0F172A" : "#64748B",
          fontSize: compact ? "12px" : "13.5px",
          fontWeight: compact ? 600 : 500,
          fontFamily: "'Inter', sans-serif",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "8px",
          cursor: disabled ? "not-allowed" : "pointer",
          outline: "none",
          boxSizing: "border-box",
          boxShadow: open ? "0 0 0 3px rgba(5, 150, 105, 0.12)" : "none",
          transition: "all 0.15s ease",
          textAlign: "left",
        }}
      >
        <span
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: 1,
          }}
        >
          {selectedOption ? selectedOption.label : placeholder}
        </span>
        <ChevronDown
          size={compact ? 14 : 16}
          color={open ? "#059669" : "#64748B"}
          style={{
            flexShrink: 0,
            transform: open ? "rotate(180deg)" : "rotate(0deg)",
            transition: "transform 0.15s ease",
          }}
        />
      </button>

      {open && (
        <div
          role="listbox"
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: 0,
            right: 0,
            minWidth: "180px",
            maxHeight: `${menuMaxHeight}px`,
            overflowY: "auto",
            background: "#FFFFFF",
            border: "1.5px solid #A7F3D0",
            borderRadius: "12px",
            boxShadow: "0 12px 28px rgba(6, 95, 70, 0.14)",
            padding: "5px",
            zIndex: 1200,
            boxSizing: "border-box",
          }}
        >
          {options.map((opt) => {
            const isSelected = opt.value === value;
            return (
              <button
                key={opt.value}
                type="button"
                role="option"
                aria-selected={isSelected}
                onClick={() => {
                  onChange(opt.value);
                  setOpen(false);
                }}
                style={{
                  width: "100%",
                  padding: compact ? "7px 10px" : "9px 12px",
                  border: "none",
                  borderRadius: "8px",
                  background: isSelected ? "#ECFDF5" : "transparent",
                  color: isSelected ? "#065F46" : "#1E293B",
                  fontSize: compact ? "12px" : "13px",
                  fontWeight: isSelected ? 700 : 500,
                  fontFamily: "'Inter', sans-serif",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "8px",
                  textAlign: "left",
                  cursor: "pointer",
                  transition: "background 0.12s ease",
                }}
                onMouseEnter={(e) => {
                  if (!isSelected) e.currentTarget.style.background = "#F0FDF4";
                }}
                onMouseLeave={(e) => {
                  if (!isSelected) e.currentTarget.style.background = "transparent";
                }}
              >
                <span
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {opt.label}
                </span>
                {isSelected && <Check size={14} color="#059669" style={{ flexShrink: 0 }} />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

