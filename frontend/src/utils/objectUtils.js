export const cloneDeep = (value) => {
  if (Array.isArray(value)) {
    return value.map(cloneDeep);
  }

  if (value && typeof value === 'object') {
    return Object.entries(value).reduce((acc, [key, val]) => {
      acc[key] = cloneDeep(val);
      return acc;
    }, {});
  }

  return value;
};

export const mergeDeep = (base, override) => {
  const result = cloneDeep(base);

  if (!override || typeof override !== 'object') {
    return result;
  }

  Object.entries(override).forEach(([key, value]) => {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const baseValue = result[key];
      result[key] = mergeDeep(baseValue ?? {}, value);
      return;
    }

    if (Array.isArray(value)) {
      result[key] = value.map(cloneDeep);
      return;
    }

    result[key] = value;
  });

  return result;
};
