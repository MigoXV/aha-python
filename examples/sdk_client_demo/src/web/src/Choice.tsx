import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

type Option = { value: string; label: string };
type Props = {
  id: string;
  value: string;
  options: Option[];
  onChange: (value: string) => void;
  disabled?: boolean;
  editable?: boolean;
  placeholder?: string;
  describedBy?: string;
};

function Chevron() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

export default function Choice({
  id,
  value,
  options,
  onChange,
  disabled = false,
  editable = false,
  placeholder,
  describedBy,
}: Props) {
  const [open, setOpen] = useState(false);
  const [filtering, setFiltering] = useState(false);
  const [active, setActive] = useState(-1);
  const control = useRef<HTMLInputElement | HTMLButtonElement | null>(null);
  const list = useRef<HTMLUListElement | null>(null);
  const search = useRef({ text: "", time: 0 });
  const expanded = open && !disabled;
  const visible = filtering
    ? options.filter((option) =>
        option.label.toLowerCase().includes(value.toLowerCase()),
      )
    : options;
  const activeOption = expanded ? visible[active] : undefined;
  const label = options.find((option) => option.value === value)?.label ?? value;

  useEffect(() => {
    if (activeOption)
      list.current?.children[active]?.scrollIntoView({ block: "nearest" });
  }, [active, activeOption]);

  function show() {
    setFiltering(false);
    setActive(options.findIndex((option) => option.value === value));
    setOpen(true);
  }

  function choose(option: Option) {
    onChange(option.value);
    setOpen(false);
    setFiltering(false);
    control.current?.focus();
  }

  function keyDown(event: KeyboardEvent<HTMLInputElement | HTMLButtonElement>) {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Escape") {
      if (expanded) event.preventDefault();
      setOpen(false);
      return;
    }
    if (event.key === "Tab") {
      setOpen(false);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!expanded) {
        show();
        const selected = options.findIndex((option) => option.value === value);
        setActive(
          selected >= 0
            ? selected
            : event.key === "ArrowDown" ? 0 : options.length - 1,
        );
      } else {
        const next = event.key === "ArrowDown"
          ? Math.min(active + 1, visible.length - 1)
          : active < 0 ? visible.length - 1 : Math.max(active - 1, 0);
        setActive(next);
      }
      return;
    }
    if ((event.key === "Home" || event.key === "End") && !editable) {
      event.preventDefault();
      setOpen(true);
      setActive(event.key === "Home" ? 0 : visible.length - 1);
      return;
    }
    if (event.key === "Enter" || (!editable && event.key === " ")) {
      if (expanded) {
        event.preventDefault();
        if (activeOption) choose(activeOption);
        else setOpen(false);
      } else if (!editable) {
        event.preventDefault();
        show();
      }
      return;
    }
    if (
      !editable && event.key.length === 1 &&
      !event.ctrlKey && !event.metaKey && !event.altKey
    ) {
      event.preventDefault();
      const now = event.timeStamp;
      const text = now - search.current.time < 750
        ? search.current.text + event.key
        : event.key;
      search.current = { text, time: now };
      const index = options.findIndex((option) =>
        option.label.toLowerCase().startsWith(text.toLowerCase()),
      );
      setOpen(true);
      setActive(index);
    }
  }

  const attributes = {
    id,
    role: "combobox" as const,
    "aria-labelledby": `${id}-label`,
    "aria-describedby": describedBy,
    "aria-expanded": expanded,
    "aria-controls": `${id}-options`,
    "aria-haspopup": "listbox" as const,
    "aria-activedescendant": activeOption ? `${id}-option-${active}` : undefined,
    disabled,
    onKeyDown: keyDown,
  };

  return (
    <div
      className={`choice ${editable ? "choice-editable" : ""}`}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      {editable ? (
        <div className="choice-input">
          <input
            {...attributes}
            ref={(element) => { control.current = element; }}
            value={value}
            required
            autoComplete="off"
            aria-autocomplete="list"
            placeholder={placeholder}
            onFocus={show}
            onClick={() => { if (!expanded) show(); }}
            onChange={(event) => {
              onChange(event.target.value);
              setFiltering(true);
              setActive(-1);
              setOpen(true);
            }}
          />
          <button
            type="button"
            className="choice-toggle"
            tabIndex={-1}
            aria-label="展开模型列表"
            disabled={disabled}
            onPointerDown={(event) => event.preventDefault()}
            onClick={() => {
              control.current?.focus();
              if (expanded) setOpen(false);
              else show();
            }}
          >
            <Chevron />
          </button>
        </div>
      ) : (
        <button
          {...attributes}
          ref={(element) => { control.current = element; }}
          type="button"
          className="choice-trigger"
          onClick={() => expanded ? setOpen(false) : show()}
        >
          <span>{label}</span>
          <Chevron />
        </button>
      )}
      <div className="choice-popup" hidden={!expanded}>
        <ul
          id={`${id}-options`}
          ref={list}
          role="listbox"
          aria-labelledby={`${id}-label`}
        >
          {visible.map((option, index) => (
            <li
              key={option.value}
              id={`${id}-option-${index}`}
              role="option"
              aria-selected={option.value === value}
              className={`choice-option ${index === active ? "active" : ""}`}
              onPointerMove={() => setActive(index)}
              onPointerDown={(event) => event.preventDefault()}
              onClick={() => choose(option)}
            >
              <span>{option.label}</span>
              {option.value === value && (
                <span className="choice-check" aria-hidden="true">✓</span>
              )}
            </li>
          ))}
        </ul>
        {!visible.length && (
          <p className="choice-empty" role="status">
            {options.length
              ? "没有匹配模型，可直接使用输入的 slug"
              : "暂无模型候选，可手动填写 slug"}
          </p>
        )}
      </div>
    </div>
  );
}
