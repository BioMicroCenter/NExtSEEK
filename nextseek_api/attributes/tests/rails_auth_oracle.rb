require "json"
require "openssl"

def canonical(value)
  case value
  when Hash
    "{" + value.keys.sort.map { |key| JSON.generate(key) + ":" + canonical(value[key]) }.join(",") + "}"
  when Array
    "[" + value.map { |item| canonical(item) }.join(",") + "]"
  else
    JSON.generate(value)
  end
end

mode = ARGV.fetch(0)
if mode == "seed"
  passwords = {}
  tokens = {}
  revoked_token = nil
  ActiveRecord::Base.transaction do
    Role.delete_all
    User.delete_all
    Person.delete_all
    cases = [
      ["ordinary-user", false, nil], ["project-admin", false, 4],
      ["programme-admin", false, 32], ["system-admin", true, 1],
      ["django-superuser-decoy-role", false, 999], ["revoked-user", false, nil],
      ["creator-admin", true, 1], ["other-admin", true, 1], ["valid-admin", true, 1],
      ["ambiguous-role-user", true, 1]
    ]
    role_rows = []
    cases.each_with_index do |(login, _admin, role_type_id), index|
      password = "boundary-#{index}"
      person = Person.create!(:first_name => "Attribute", :last_name => "Case#{index}",
                              :email => "#{login}@attribute-boundary.example")
      user = User.create!(:login => login, :person => person, :password => password,
                          :password_confirmation => password)
      role_rows << {"person_id" => person.id, "role_type_id" => role_type_id,
                    "scope_type" => nil, "scope_id" => nil,
                    "created_at" => Time.now.utc, "updated_at" => Time.now.utc} if role_type_id
      api_token = ApiToken.create!(:user => user, :title => "attribute-boundary")
      passwords[login] = password
      tokens[login] = api_token.token
      if login == "revoked-user"
        revoked_token = api_token.token
        api_token.destroy!
      end
    end
    # Raw insertion is intentional for the negative project/programme/decoy rows: the
    # oracle is Person#is_admin? over persisted role_type IDs, not role-grant validation.
    Role.insert_all!(role_rows)
    # Two independent decoys exercise the forbidden shortcuts.  The Rails
    # predicate remains false because neither row has the system-admin role type.
    decoy = Person.joins(:user).find_by!(users: {login: "django-superuser-decoy-role"})
    decoy.update_column(:roles_mask, 0x7fffffff) if decoy.has_attribute?(:roles_mask)
  end
  puts JSON.generate({"passwords" => passwords, "tokens" => tokens,
                      "revoked_token" => revoked_token})
  exit 0
end

raise "unknown mode" unless mode == "oracle"
rows = Person.order(:id).map { |person| {"person_id" => person.id, "is_admin" => !!person.is_admin?} }
payload = {"input_row_ids" => rows.map { |row| row["person_id"] }, "rows" => rows}
signature = OpenSSL::HMAC.hexdigest("SHA256", [ENV.fetch("ATTRIBUTE_ORACLE_KEY")].pack("H*"), canonical(payload))
puts JSON.generate(payload.merge("signature" => signature))
