require 'json'
payload = JSON.parse(ENV.fetch("NEXTSEEK_PAYLOAD"))

admin = User.joins(:person).detect { |u| u.person&.is_admin? }
raise 'No SEEK admin user available for rails runner' if admin.nil?
User.with_current_user(admin) do

  user = User.find(payload['user_id'])
  if user.active?
    user.send(:make_activation_code)
    user.save(validate: false)
  end
  puts({ ok: true, user_id: user.id, compensated: true }.to_json)
rescue => e
  puts({ ok: false, error: e.message, class: e.class.name }.to_json)
  exit 1

end
