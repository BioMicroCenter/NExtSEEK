class TaskFamily {
  name             string
  description      string
  example_queries  string[]
}

class RouteCapability {
  route_name     string
  description    string
  tools          string[]
  best_for       string?
  not_for        string?
  task_families  TaskFamily[]
}

class RouterInput {
  user_query  string
  routes      RouteCapability[]
}

enum Route {
  NextseekQuery @alias("nextseek_query")
  ContainerCC   @alias("container_cc")
  Unrelated     @alias("unrelated")
}

enum ModelClass {
  Sonnet @alias("sonnet")
  Haiku  @alias("haiku")
  Opus   @alias("opus")
}

class RouterDecision {
  route        Route
  model_class  ModelClass?
  reasoning    string
}

function RouteQuery(input: RouterInput) -> RouterDecision {
  client GCPReasoner
  prompt #"
    You route a user query to one of the available execution routes for the
    DMAC research assistant. Use the route's declared capabilities to decide.

    {% for route in input.routes %}
    ## {{ route.route_name }}
    {{ route.description }}

    Tools / plugins / skills: {{ route.tools|join(", ") }}
    {% if route.best_for %}Best for: {{ route.best_for }}{% endif %}
    {% if route.not_for %}Avoid for: {{ route.not_for }}{% endif %}

    Task families:
    {% for tf in route.task_families %}
    - {{ tf.name }} — {{ tf.description }}
      Example queries:
      {% for q in tf.example_queries %}
      * "{{ q }}"
      {% endfor %}
    {% endfor %}
    {% endfor %}

    User query: {{ input.user_query }}

    If the query has no connection to NExtSEEK, the BioMicro Center lab, or
    the user's research data — for example general world trivia, celebrity or
    pop-culture gossip, current events, or chit-chat unrelated to the lab's
    samples, studies, code, or files — select `unrelated`. Do NOT route such
    queries to `container_cc` or `nextseek_query`.

    {{ ctx.output_format }}
  "#
}
