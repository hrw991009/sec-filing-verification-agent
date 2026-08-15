import type { SVGProps } from "react";

export type IconName =
  | "attachment"
  | "bolt"
  | "chevron"
  | "close"
  | "document"
  | "download"
  | "edit"
  | "image"
  | "menu"
  | "message"
  | "more"
  | "new"
  | "refresh"
  | "search"
  | "send"
  | "settings"
  | "sparkles"
  | "stop"
  | "trash"
  | "user";

const paths: Record<IconName, string> = {
  attachment: "M12 5.5v12a4 4 0 0 1-8 0v-11a6 6 0 0 1 12 0v10a8 8 0 0 1-16 0V8.5",
  bolt: "m13 2-9 12h8l-1 8 9-12h-8l1-8Z",
  chevron: "m9 18 6-6-6-6",
  close: "M18 6 6 18M6 6l12 12",
  document: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Zm0 0v6h6M8 13h8M8 17h6",
  download: "M12 3v12m0 0 5-5m-5 5-5-5M5 21h14",
  edit: "M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4Z",
  image:
    "M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Zm0 11 5-5 4 4 2-2 7 7M16.5 8.5h.01",
  menu: "M4 6h16M4 12h16M4 18h16",
  message: "M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  new: "M12 5v14M5 12h14",
  refresh: "M20 11a8.1 8.1 0 0 0-15.5-2M4 4v5h5m-5 4a8.1 8.1 0 0 0 15.5 2M20 20v-5h-5",
  search: "m21 21-4.35-4.35M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z",
  send: "m22 2-7 20-4-9-9-4Zm0 0L11 13",
  settings:
    "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.86 2.86-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6l-.04.08h-4l-.04-.08a1.7 1.7 0 0 0-1-.6 1.7 1.7 0 0 0-1.88.34l-.06.06-2.86-2.86.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1l-.08-.04v-4L4 9.92a1.7 1.7 0 0 0 .6-1 1.7 1.7 0 0 0-.34-1.88L4.2 6.98l2.86-2.86.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6l.04-.08h4l.04.08a1.7 1.7 0 0 0 1 .6 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.86 2.86-.06.06A1.7 1.7 0 0 0 19.4 9c.08.37.29.72.6 1l.08.04v4L20 14.08c-.31.28-.52.63-.6.92Z",
  sparkles:
    "m12 3 1.4 4.1L17.5 8.5l-4.1 1.4L12 14l-1.4-4.1-4.1-1.4 4.1-1.4ZM19 14l.8 2.2L22 17l-2.2.8L19 20l-.8-2.2L16 17l2.2-.8ZM5 14l.6 1.4L7 16l-1.4.6L5 18l-.6-1.4L3 16l1.4-.6Z",
  stop: "M7 7h10v10H7z",
  trash: "M3 6h18M8 6V4h8v2m-9 0 1 15h8l1-15M10 10v7M14 10v7",
  user: "M20 21a8 8 0 0 0-16 0M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10Z",
};

export function Icon({ name, ...props }: { readonly name: IconName } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height="20"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.8"
      viewBox="0 0 24 24"
      width="20"
      {...props}
    >
      <path d={paths[name]} />
    </svg>
  );
}
