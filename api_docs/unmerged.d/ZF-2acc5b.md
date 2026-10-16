* [`GET /attachments`](/api/get-attachments), [`GET /events`](/api/get-events),
  [`PATCH /messages/{message_id}`](/api/update-message): Attachment objects
  have a new `post_ids` field, listing the pages posts that link to the
  uploaded file among those its owner may see. A pages post's link keeps
  a file in use as a message's does: the file is not deleted as unused,
  and an edited message's `detached_uploads` leaves it out.
* [`GET /events`](/api/get-events): An `attachment` event with
  `"op": "update"` is now also sent to a file's owner when the pages
  posts that link to it change.
