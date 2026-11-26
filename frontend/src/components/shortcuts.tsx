import { Modal } from "./ui";

export function ShortcutHelp({ items, onClose }: { items: [string, string][]; onClose: () => void }) {
  const mac = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);
  const show = (k: string) => k.replace("mod+", mac ? "⌘ " : "Ctrl ").replace("shift+", "⇧ ").replace("space", "Space").replace("enter", "Enter");
  return (
    <Modal
      title="Keyboard shortcuts"
      onClose={onClose}
      actions={
        <button className="btn" onClick={onClose}>
          Close
        </button>
      }
    >
      <div className="shortcut-list">
        {items.map(([k, d]) => (
          <div key={k} className="row" style={{ justifyContent: "space-between" }}>
            <span className="small">{d}</span>
            <span className="row" style={{ gap: 4 }}>
              {k.split(" / ").map((x) => (
                <kbd key={x}>{show(x)}</kbd>
              ))}
            </span>
          </div>
        ))}
      </div>
    </Modal>
  );
}
