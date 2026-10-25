    static final class RuleContext {
        private final List<String> messages = new ArrayList<>();
        public void message(String value) { if (value != null) messages.add(value); }
        public List<String> messages() { return List.copyOf(messages); }
        public void abort() {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,
                messages.isEmpty() ? "A rekord validálása sikertelen." : messages.get(messages.size() - 1));
        }
    }
