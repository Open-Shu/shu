export function pluginDisplayName(p) {
  if (!p || typeof p !== 'object') return '';
  return p.display_name || p.name || '';
}

export function pluginPrimaryLabel(p) {
  const base = pluginDisplayName(p);
  const suffix = p && p.enabled === false ? ' (disabled)' : '';
  return `${base}${suffix}`;
}

