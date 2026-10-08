/** Activate the current confirmation, leaving text editing and native controls intact. */
export function confirmOnEnter(event: KeyboardEvent) {
  if (event.key !== "Enter" || event.defaultPrevented || event.isComposing || event.keyCode === 229 ||
      event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
  const target = event.target;
  if (!(target instanceof Element) || !target.isConnected) return;

  const visible = (element: HTMLElement) => element.getClientRects().length > 0 &&
    getComputedStyle(element).visibility === "visible" && !element.closest('[hidden],[inert],[aria-hidden="true"]');
  const dialogs = Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"]')).filter(visible);
  const dialog = dialogs.at(-1);
  // An open dialog owns the shortcut, including when focus has escaped to the page.
  if (dialog && !dialog.contains(target)) { event.preventDefault(); return; }
  const form = target.closest("form");
  const scope = form || dialog || document.querySelector("main");
  if (!scope) return;
  const confirms = Array.from(scope.querySelectorAll<HTMLButtonElement>('button[data-enter-confirm="true"]')).filter(visible);

  if (target.closest('textarea,[contenteditable]:not([contenteditable="false"]),a[href],select,summary,[role="combobox"],[role="listbox"],[role="menu"]')) return;
  if (target instanceof HTMLInputElement && !["text", "password", "email", "url", "tel", "search", "number"].includes(target.type)) return;
  // Holding Enter must not submit repeatedly or confirm a newly opened dialog.
  if (event.repeat) { if (confirms.length) event.preventDefault(); return; }
  if (target.closest('button,[role="button"]')) return;
  // Filter fields outside a form are not a request to save unrelated page changes.
  if (!dialog && !form && target instanceof HTMLInputElement) return;
  if (confirms.length !== 1) return;
  event.preventDefault();
  const button = confirms[0];
  if (!button.matches(':disabled') && button.getAttribute('aria-disabled') !== 'true') button.click();
}
