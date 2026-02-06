import React from 'react';
import FeedDialog from './FeedDialog';

export default function FeedCreateDialog(props) {
  // Backwards-compatible wrapper to reduce churn in parents
  return <FeedDialog mode="create" {...props} />;
}
