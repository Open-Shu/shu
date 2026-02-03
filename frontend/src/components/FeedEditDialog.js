import React from "react";
import FeedDialog from "./FeedDialog";

export default function FeedEditDialog(props) {
  // Backwards-compatible wrapper to reduce churn in parents
  return <FeedDialog mode="edit" {...props} />;
}
