require 'json'
payload = JSON.parse(ENV.fetch("NEXTSEEK_PAYLOAD"))

admin = User.joins(:person).detect { |u| u.person&.is_admin? }
raise 'No SEEK admin user available for rails runner' if admin.nil?
User.with_current_user(admin) do

  project = Project.find(payload['project_id'])
  institution = Institution.find(payload['institution_id'])
  person = Person.create!(
    first_name: payload['first_name'],
    last_name: payload['last_name'],
    email: payload['email']
  )
  person.add_to_project_and_institution(project, institution)
  user = User.new(
    login: payload['login'],
    password: payload['password'],
    password_confirmation: payload['password_confirmation'],
    email: person.email
  )
  user.person = person
  user.save!
  user.activate if payload.fetch('activate', true) && !user.active?
  puts({
    ok: true,
    user_id: user.id,
    person_id: person.id,
    login: user.login,
    email: person.email,
    active: user.active?,
    project_id: project.id,
    institution_id: institution.id
  }.to_json)
rescue ActiveRecord::RecordInvalid => e
  puts({ ok: false, error: e.message, detail: e.record.errors.full_messages.join('; ') }.to_json)
  exit 1
rescue => e
  puts({ ok: false, error: e.message, class: e.class.name }.to_json)
  exit 1

end
