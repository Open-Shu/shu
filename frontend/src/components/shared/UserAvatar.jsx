import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Avatar } from '@mui/material';

// Centralizes how we pick and render the user's avatar image
// Props: user, size (number, px), fallbackChar (string)
const MAX_RETRIES = 2;

const addCacheBuster = (url, n) => {
  try {
    if (!url) return url;
    const u = new URL(url, window.location.origin);
    u.searchParams.set('shu_avoid_cache', String(Date.now()));
    if (typeof n === 'number') {
      u.searchParams.set('retry', String(n));
    }
    return u.toString();
  } catch (_) {
    // If URL constructor fails (cross-origin without base), fallback to string concat
    const sep = url.includes('?') ? '&' : '?';
    const retryPart = typeof n === 'number' ? `&retry=${n}` : '';
    return `${url}${sep}shu_avoid_cache=${Date.now()}${retryPart}`;
  }
};

const UserAvatar = ({ user, size = 32, fallbackChar = 'U', sx = {} }) => {
  const baseUrl = user?.picture_url || null;
  const [retry, setRetry] = useState(0);
  const [broken, setBroken] = useState(false);
  const lastBaseRef = useRef(baseUrl);

  // Reset broken/retry if the underlying URL changes
  useEffect(() => {
    if (lastBaseRef.current !== baseUrl) {
      lastBaseRef.current = baseUrl;
      setRetry(0);
      setBroken(false);
    }
  }, [baseUrl]);

  const effectiveSrc = useMemo(() => {
    if (!baseUrl || broken) return null;
    return retry > 0 ? addCacheBuster(baseUrl, retry) : baseUrl;
  }, [baseUrl, broken, retry]);

  const handleError = () => {
    if (retry < MAX_RETRIES) {
      // try again with cache buster
      setRetry((n) => n + 1);
    } else {
      setBroken(true);
    }
  };

  return (
    <Avatar
      src={effectiveSrc || undefined}
      imgProps={{
        referrerPolicy: 'no-referrer',
        crossOrigin: 'anonymous',
        onError: handleError,
      }}
      sx={{ width: size, height: size, backgroundColor: 'background.paper', color: 'primary.main', fontWeight: 600, ...sx }}
    >
      {fallbackChar}
    </Avatar>
  );
};

export default UserAvatar;
