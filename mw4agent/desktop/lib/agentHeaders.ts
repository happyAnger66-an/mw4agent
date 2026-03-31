/**
 * Avatar images served from ``public/icons/headers/``.
 * When adding files, copy them into that folder and append the basename here.
 */

export const AGENT_HEADER_FILES: string[] = [
  "14 柿子.png",
  "icon-a-124.png",
  "天妇罗.png",
  "爱因斯坦.png",
  "牛人.png",
  "生煎.png",
  "花生.png",
  "工程师.png",
  "工程师-程序员.png",
  "manager.png",
  "manager1.png",
  "工人.png",
];

export function agentHeaderSrc(basename: string): string {
  return `/icons/headers/${encodeURIComponent(basename.trim())}`;
}
